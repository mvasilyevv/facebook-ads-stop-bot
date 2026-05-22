# -*- coding: utf-8 -*-
"""Роутер для трёхуровневой AI-аналитики с Postgres кэшированием."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from apps.api.deps import get_db
from core.ai_assistant.client import AIUnavailableError, get_ai_client
from core.models import (
    AdSnapshot,
    AICache,
    AlertEvent,
    DisableTask,
    EnableTask,
    FbAd,
    FbAdset,
    FbCampaign,
    Offer,
)

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/ai", tags=["ai"])

# TTL политики кэширования для различных типов блоков
TTL_POLICIES = {
    "briefing": timedelta(minutes=5),
    "offers": timedelta(minutes=5),
    "alerts": timedelta(minutes=5),
    "alert_inline": timedelta(minutes=30),
    "pacing": timedelta(minutes=10),
    "heatmap": timedelta(hours=1),
    "reasons": timedelta(hours=1),
    "cpl_timeline": timedelta(hours=1),
    "history": timedelta(hours=1),
}


class AIAnalyzeRequest(BaseModel):
    block_type: str = Field(
        ...,
        description="Тип блока аналитики (briefing, offers, alerts, alert_inline, pacing, heatmap, reasons, cpl_timeline, history)",
    )
    scope_key: str = Field(
        "global", description="Ключ области (например, 'global' или UUID алерта)"
    )
    force_refresh: bool = Field(False, description="Принудительно обновить кэш")
    client_data: dict[str, Any] | None = Field(
        None,
        description=(
            "Данные графика/таблицы, которые видит пользователь в этот момент. "
            "Если переданы — AI анализирует именно их, а не выборку из БД."
        ),
    )


class AIAnalyzeResponse(BaseModel):
    content: str = Field(..., description="Результат анализа от AI в формате Markdown")
    cached_at: str | None = Field(None, description="Время кэширования в ISO формате")
    expires_at: str = Field(..., description="Время истечения срока кэша в ISO формате")
    warning: str | None = Field(None, description="Предупреждение о работе системы")


def calculate_hash(data: Any) -> str:
    """Вычисляет SHA256 хэш от сериализованных данных для авто-инвалидации кэша при изменении метрик."""
    serialized = json.dumps(data, default=str, sort_keys=True)
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


async def gather_context_data(db: AsyncSession, block_type: str, scope_key: str) -> dict[str, Any]:
    """Сбор метрик из БД на основе типа блока."""
    data = {}

    if block_type == "briefing":
        # Метрики для глобального брифинга
        active_offers = await db.scalars(select(Offer).where(Offer.is_active.is_(True)))
        offers_list = [{"code": o.code, "cpa": float(o.cpa_amount)} for o in active_offers]

        # Находим время последнего сканирования
        last_scan = await db.scalar(select(func.max(AdSnapshot.last_observed_at)))
        scan_cutoff = last_scan - timedelta(minutes=30) if last_scan else None

        # Собираем активные кампании и их расходы за сегодня через связь с AdSnapshot (только свежие за 30 минут)
        campaign_spends_query = (
            select(FbCampaign.campaign_name, AdSnapshot.spend, AdSnapshot.delivery_status)
            .join(FbAdset, FbCampaign.id == FbAdset.campaign_id)
            .join(FbAd, FbAdset.id == FbAd.adset_id)
            .join(AdSnapshot, FbAd.id == AdSnapshot.ad_id)
        )
        if scan_cutoff:
            campaign_spends_query = campaign_spends_query.where(
                AdSnapshot.last_observed_at >= scan_cutoff
            )

        campaigns_res = await db.execute(campaign_spends_query)
        campaign_map = {}
        for name, spend, delivery_status in campaigns_res.all():
            is_ad_active = delivery_status != "OFF"
            if is_ad_active:
                campaign_map[name] = campaign_map.get(name, 0.0) + float(spend or 0)

        campaigns_list = [{"name": name, "spend": spend} for name, spend in campaign_map.items()]

        # Недавние 5 алертов с подгрузкой офферов (только за последние 24 часа от последнего сканирования)
        alerts_query = select(AlertEvent, Offer.code.label("offer_code")).outerjoin(
            Offer, AlertEvent.offer_id == Offer.id
        )
        if last_scan:
            alerts_query = alerts_query.where(
                AlertEvent.created_at >= last_scan - timedelta(hours=24)
            )

        alerts_query = alerts_query.order_by(AlertEvent.created_at.desc()).limit(5)

        alerts_res = await db.execute(alerts_query)
        alerts_list = [
            {
                "id": str(al.id),
                "offer_code": offer_code or "N/A",
                "rule": ", ".join(al.matched_rule_codes or []) or al.reason_title or "N/A",
                "reason": al.reason_title or "N/A",
                "created_at": al.created_at.isoformat(),
            }
            for al, offer_code in alerts_res.all()
        ]

        data = {
            "active_offers": offers_list,
            "campaigns": campaigns_list,
            "recent_alerts": alerts_list,
            "timestamp": datetime.now(UTC).isoformat(),
        }

    elif block_type == "offers":
        offers = await db.scalars(select(Offer))
        offers_list = []
        for o in offers:
            offers_list.append(
                {
                    "code": o.code,
                    "cpa": float(o.cpa_amount),
                    "payout": float(o.payout_per_deposit or 0),
                    "is_active": o.is_active,
                    "geo": o.geo_code or "N/A",
                }
            )
        data = {"offers": offers_list}

    elif block_type == "alerts":
        alerts_query = (
            select(AlertEvent, Offer.code.label("offer_code"))
            .outerjoin(Offer, AlertEvent.offer_id == Offer.id)
            .order_by(AlertEvent.created_at.desc())
            .limit(15)
        )
        alerts_res = await db.execute(alerts_query)
        data = {
            "alerts": [
                {
                    "id": str(al.id),
                    "offer_code": offer_code or "N/A",
                    "rule": ", ".join(al.matched_rule_codes or []) or al.reason_title or "N/A",
                    "reason": al.reason_title or "N/A",
                    "stage": al.stage.value if hasattr(al.stage, "value") else str(al.stage),
                    "created_at": al.created_at.isoformat(),
                }
                for al, offer_code in alerts_res.all()
            ]
        }

    elif block_type == "alert_inline":
        # Детальная информация по конкретному алерту
        try:
            alert_uuid = UUID(scope_key)
        except ValueError as exc:
            raise HTTPException(
                status_code=400, detail=f"Неверный UUID алерта: {scope_key}"
            ) from exc

        alert_query = (
            select(AlertEvent, Offer.code.label("offer_code"), FbAd.ad_name.label("ad_name"))
            .outerjoin(Offer, AlertEvent.offer_id == Offer.id)
            .outerjoin(FbAd, AlertEvent.ad_id == FbAd.id)
            .where(AlertEvent.id == alert_uuid)
        )
        row = (await db.execute(alert_query)).first()
        if not row:
            raise HTTPException(status_code=404, detail=f"Алерт {scope_key} не найден")

        al, offer_code, ad_name = row
        data = {
            "alert": {
                "id": str(al.id),
                "offer_code": offer_code or "N/A",
                "ad_name": ad_name or "N/A",
                "rule": ", ".join(al.matched_rule_codes or []) or al.reason_title or "N/A",
                "reason": al.reason_title or "N/A",
                "stage": al.stage.value if hasattr(al.stage, "value") else str(al.stage),
                "created_at": al.created_at.isoformat(),
            }
        }

    elif block_type == "pacing":
        # Находим время последнего сканирования
        last_scan = await db.scalar(select(func.max(AdSnapshot.last_observed_at)))
        scan_cutoff = last_scan - timedelta(minutes=30) if last_scan else None

        # Собираем данные по кампаниям, объединяя с AdSnapshot (только свежие за 30 минут)
        q = (
            select(
                FbCampaign.campaign_name,
                FbCampaign.offer_code,
                AdSnapshot.spend,
                AdSnapshot.delivery_status,
            )
            .join(FbAdset, FbCampaign.id == FbAdset.campaign_id)
            .join(FbAd, FbAdset.id == FbAd.adset_id)
            .join(AdSnapshot, FbAd.id == AdSnapshot.ad_id)
        )
        if scan_cutoff:
            q = q.where(AdSnapshot.last_observed_at >= scan_cutoff)

        res = await db.execute(q)
        campaign_map = {}
        for name, offer_code, spend, delivery_status in res.all():
            is_ad_active = delivery_status != "OFF"
            if name not in campaign_map:
                campaign_map[name] = {
                    "name": name,
                    "offer_code": offer_code,
                    "is_active": False,
                    "daily_spend": 0.0,
                    "lifetime_spend": 0.0,
                }
            item = campaign_map[name]
            if is_ad_active:
                item["is_active"] = True
            item["daily_spend"] += float(spend or 0)
            item["lifetime_spend"] += float(spend or 0)

        data = {"campaigns": list(campaign_map.values())}

    elif block_type == "heatmap":
        # Подсчет алертов по дням недели и часам за последние 14 дней
        # Находим максимальную дату алерта в БД для корректной работы в демо-окружении
        max_date = await db.scalar(select(func.max(AlertEvent.created_at)))
        if max_date:
            cutoff = max_date - timedelta(days=14)
        else:
            cutoff = datetime.now(UTC) - timedelta(days=14)
        alerts = await db.scalars(select(AlertEvent).where(AlertEvent.created_at >= cutoff))

        # Строим матрицу 7 дней * 24 часа
        matrix = [[0 for _ in range(24)] for _ in range(7)]
        for al in alerts:
            # Получаем день недели (0-6) и час (0-23)
            dt = al.created_at
            matrix[dt.weekday()][dt.hour] += 1

        data = {"matrix": matrix}

    elif block_type == "reasons":
        # Распределение причин остановок
        alerts = await db.scalars(
            select(AlertEvent).order_by(AlertEvent.created_at.desc()).limit(100)
        )
        reasons_count: dict[str, int] = {}
        for al in alerts:
            reason = al.reason_title or "Неизвестная причина"
            reasons_count[reason] = reasons_count.get(reason, 0) + 1
        data = {"reasons": reasons_count}

    elif block_type == "cpl_timeline":
        # Временная шкала CPL за последние 10 дней
        # Находим максимальную дату алерта в БД для корректной работы в демо-окружении
        max_date = await db.scalar(select(func.max(AlertEvent.created_at)))
        if max_date:
            cutoff = max_date - timedelta(days=10)
        else:
            cutoff = datetime.now(UTC) - timedelta(days=10)
        alerts_query = (
            select(AlertEvent, Offer.code.label("offer_code"))
            .outerjoin(Offer, AlertEvent.offer_id == Offer.id)
            .where(AlertEvent.created_at >= cutoff)
        )
        alerts_res = await db.execute(alerts_query)

        timeline: dict[str, dict[str, Any]] = {}
        for al, offer_code in alerts_res.all():
            day_str = al.created_at.date().isoformat()
            if day_str not in timeline:
                timeline[day_str] = {"alerts_count": 0, "offers": set()}
            timeline[day_str]["alerts_count"] += 1
            if offer_code:
                timeline[day_str]["offers"].add(offer_code)

        formatted_timeline = [
            {
                "date": day,
                "alerts_count": val["alerts_count"],
                "offers_affected": list(val["offers"]),
            }
            for day, val in sorted(timeline.items())
        ]
        data = {"timeline": formatted_timeline}

    elif block_type == "history":
        disable_tasks = await db.scalars(
            select(DisableTask)
            .options(selectinload(DisableTask.fb_ad))
            .order_by(DisableTask.created_at.desc())
            .limit(10)
        )
        enable_tasks = await db.scalars(
            select(EnableTask)
            .options(selectinload(EnableTask.fb_ad))
            .order_by(EnableTask.created_at.desc())
            .limit(10)
        )

        data = {
            "disable_tasks": [
                {
                    "id": str(t.id),
                    "fb_ad_id": t.fb_ad.fb_ad_id if t.fb_ad else None,
                    "status": t.status.value if hasattr(t.status, "value") else str(t.status),
                    "created_at": t.created_at.isoformat(),
                }
                for t in disable_tasks
            ],
            "enable_tasks": [
                {
                    "id": str(t.id),
                    "fb_ad_id": t.fb_ad.fb_ad_id if t.fb_ad else None,
                    "status": t.status.value if hasattr(t.status, "value") else str(t.status),
                    "created_at": t.created_at.isoformat(),
                }
                for t in enable_tasks
            ],
        }
    else:
        raise HTTPException(status_code=400, detail=f"Неподдерживаемый тип блока: {block_type}")

    return data


def build_ai_prompt(block_type: str, data: dict[str, Any]) -> tuple[str, str]:
    """Формирует системный и пользовательский промпт на русском языке.

    Если в data передан client_snapshot (source == "client_snapshot"), промпт
    рассказывает AI, что данные взяты ровно те, которые сейчас отображены
    пользователю на графике — это устраняет рассинхрон UI↔AI.
    """
    system_prompt = (
        "Вы — профессиональный AI-аналитик Neo Control Room в панели AdGuard FB Bot. "
        "Ваша цель — анализировать метрики закупки трафика, выявлять аномалии, давать краткие и полезные советы на русском языке. "
        "Ответ должен быть написан в профессиональном, сжатом стиле в формате Markdown. "
        "Используйте списки, таблицы и выделения для структурирования информации. "
        "Не лейте воду, пишите строго по делу."
    )

    is_client_snapshot = data.get("source") == "client_snapshot"
    snapshot_note = (
        "\nИсточник данных: снимок графика/таблицы, который пользователь видит прямо сейчас в UI. "
        "Анализируйте именно эти значения, не запрашивайте дополнительных данных и не упоминайте, "
        "что чего-то не хватает, если поле просто отсутствует — работайте с тем, что есть.\n"
        if is_client_snapshot
        else ""
    )

    prompt = ""
    if block_type == "briefing":
        prompt = (
            "Сделайте краткий глобальный обзор текущего состояния закупки (Global Briefing).\n"
            f"Текущие активные офферы и CPA:\n{json.dumps(data.get('active_offers'), ensure_ascii=False, indent=2)}\n\n"
            f"Активные кампании и их расходы за сегодня:\n{json.dumps(data.get('campaigns'), ensure_ascii=False, indent=2)}\n\n"
            f"Последние 5 сработавших алертов / предупреждений:\n{json.dumps(data.get('recent_alerts'), ensure_ascii=False, indent=2)}\n\n"
            "Дайте оценку эффективности, выявите критические проблемы и предложите 2-3 приоритетных действия для медиабайера."
        )
    elif block_type == "offers":
        prompt = (
            "Проанализируйте статус и показатели ваших офферов.\n"
            f"Список офферов:\n{json.dumps(data.get('offers'), ensure_ascii=False, indent=2)}\n\n"
            "Укажите, какие офферы наиболее активны, соответствуют ли CPA нормам закупки и есть ли неактивные офферы с подозрительной активностью."
        )
    elif block_type == "alerts":
        prompt = (
            "Проанализируйте последние инциденты и предупреждения бота.\n"
            f"Список недавних алертов:\n{json.dumps(data.get('alerts'), ensure_ascii=False, indent=2)}\n\n"
            "Выявите повторяющиеся паттерны или правила, которые срабатывают чаще всего. Каковы ваши рекомендации по корректировке порогов или оптимизации?"
        )
    elif block_type == "alert_inline":
        prompt = (
            "Проведите детальный разбор конкретного инцидента.\n"
            f"Данные алерта:\n{json.dumps(data.get('alert'), ensure_ascii=False, indent=2)}\n\n"
            "Объясните простыми словами, почему сработало это правило, насколько критична ситуация и какие точечные действия нужно предпринять."
        )
    elif block_type == "pacing":
        prompt = (
            "Проанализируйте распределение бюджета и скорость расхода (Pacing).\n"
            f"Данные по кампаниям:\n{json.dumps(data.get('campaigns'), ensure_ascii=False, indent=2)}\n\n"
            "Выявите перекруты или недоливы бюджета, дайте оценку скорости расхода."
        )
    elif block_type == "heatmap":
        prompt = (
            "Проанализируйте 14-дневную тепловую карту алертов.\n"
            f"Матрица 7x24 (строки - дни недели с Пн по Вс, колонки - часы 0-23, значения - кол-во алертов):\n"
            f"{json.dumps(data.get('matrix'), indent=2)}\n\n"
            "Укажите пиковые часы и дни недели, когда бот чаще всего останавливает рекламу. Дайте рекомендации по временному таргетингу."
        )
    elif block_type == "reasons":
        prompt = (
            "Проанализируйте распределение причин остановок рекламы ботом.\n"
            f"Статистика причин остановок:\n{json.dumps(data.get('reasons'), ensure_ascii=False, indent=2)}\n\n"
            "Какое стоп-правило доминирует и о чём это говорит (плохой зацеп, высокий CPL на старте, баги пикселя и т.д.)?"
        )
    elif block_type == "cpl_timeline":
        prompt = (
            "Проанализируйте динамику стоимости лида (CPL) и инцидентов.\n"
            f"Данные по дням:\n{json.dumps(data.get('timeline'), ensure_ascii=False, indent=2)}\n\n"
            "Опишите тренд: ситуация улучшается или ухудшается? Какие дни были наиболее нестабильными?"
        )
    elif block_type == "history":
        prompt = (
            "Проанализируйте историю действий бота по оптимизации.\n"
            f"Недавние задачи на отключение (Disable tasks):\n{json.dumps(data.get('disable_tasks'), ensure_ascii=False, indent=2)}\n\n"
            f"Недавние задачи на включение (Enable tasks):\n{json.dumps(data.get('enable_tasks'), ensure_ascii=False, indent=2)}\n\n"
            "Дайте оценку активности автоматики бота и успешности выполнения задач."
        )

    if is_client_snapshot and prompt:
        prompt = snapshot_note + prompt

    return system_prompt, prompt


@router.post("/analyze", response_model=AIAnalyzeResponse)
async def ai_analyze(
    body: AIAnalyzeRequest, db: AsyncSession = Depends(get_db)
) -> AIAnalyzeResponse:
    """Генерирует или извлекает из кэша AI-аналитику для Neo Control Room."""
    block_type = body.block_type
    scope_key = body.scope_key

    if block_type not in TTL_POLICIES:
        raise HTTPException(status_code=400, detail=f"Недопустимый block_type: {block_type}")

    # 1. Собираем свежие метрики (из БД или из клиентского снимка)
    if body.client_data is not None:
        metrics = {**body.client_data, "source": "client_snapshot"}
    else:
        try:
            metrics = await gather_context_data(db, block_type, scope_key)
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("Ошибка сбора метрик для AI: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=f"Ошибка сбора метрик: {str(exc)}") from exc

    # 2. Вычисляем хэш от метрик
    payload_hash = calculate_hash(metrics)

    # 3. Проверяем кэш, если не запрошено принудительное обновление
    now = datetime.now(UTC)
    if not body.force_refresh:
        cache_entry = await db.scalar(
            select(AICache).where(
                AICache.block_type == block_type,
                AICache.scope_key == scope_key,
                AICache.payload_hash == payload_hash,
                AICache.expires_at > now,
            )
        )
        if cache_entry:
            return AIAnalyzeResponse(
                content=cache_entry.content,
                cached_at=cache_entry.created_at.isoformat(),
                expires_at=cache_entry.expires_at.isoformat(),
                warning=None,
            )

    # 4. Если кэша нет или устарел — генерируем ответ с помощью AIClient
    system_prompt, user_prompt = build_ai_prompt(block_type, metrics)
    ai_client = get_ai_client()

    content = ""
    warning = None
    tokens_in = None
    tokens_out = None

    if not ai_client.is_available:
        # Если ключи API отсутствуют, возвращаем заглушку/no-op
        warning = "Предупреждение: Ключи API (Anthropic/OpenAI) не настроены. Отображаются демонстрационные данные."
        content = (
            f"### Демонстрационный анализ для блока **{block_type}**\n\n"
            "Интеграция с ИИ активна, но API ключи не заданы в переменных окружения.\n\n"
            "**Примерные выводы на основе текущих метрик:**\n"
            f"- Данные успешно агрегированы (хэш метрик: `{payload_hash[:10]}...`)\n"
            "- Система готова к работе. Пожалуйста, укажите `ANTHROPIC_API_KEY` или `OPENAI_API_KEY` в файле `.env` для полноценного анализа."
        )
    else:
        try:
            ai_response = await ai_client.chat(
                messages=[{"role": "user", "content": user_prompt}],
                system=system_prompt,
                max_tokens=1500,
            )
            content = ai_response.text
            # Извлекаем токены если доступны в raw ответе
            if ai_response.raw:
                usage = ai_response.raw.get("usage", {})
                if usage:
                    tokens_in = usage.get("input_tokens") or usage.get("prompt_tokens")
                    tokens_out = usage.get("output_tokens") or usage.get("completion_tokens")
        except AIUnavailableError as exc:
            logger.warning("AI-клиент вернул ошибку недоступности: %s", exc)
            warning = (
                f"Внимание: ИИ временно недоступен ({str(exc)}). Отображаются локальные выводы."
            )
            content = (
                f"### Локальный технический анализ для блока **{block_type}**\n\n"
                "Не удалось связаться с сервером ИИ. Выведены базовые технические показатели:\n"
                f"- Хэш состояния данных: `{payload_hash}`\n"
                f"- Зарегистрировано событий: {len(metrics.get('recent_alerts', [])) or len(metrics.get('alerts', [])) or 0}\n"
                "- Рекомендуется проверить сетевое соединение и лимиты API-ключей."
            )
        except Exception as exc:
            logger.error("Непредвиденная ошибка при запросе к AI: %s", exc, exc_info=True)
            raise HTTPException(status_code=500, detail=f"Ошибка генерации AI: {str(exc)}") from exc

    # 5. Сохраняем в кэш (даже если это заглушка, чтобы не перегружать запросы)
    ttl = TTL_POLICIES.get(block_type, timedelta(minutes=5))
    expires_at = now + ttl

    # Удаляем старый кэш для этой пары (block_type, scope_key) перед записью нового
    await db.execute(
        AICache.__table__.delete().where(
            AICache.block_type == block_type, AICache.scope_key == scope_key
        )
    )

    new_cache = AICache(
        block_type=block_type,
        scope_key=scope_key,
        payload_hash=payload_hash,
        content=content,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        expires_at=expires_at,
    )
    db.add(new_cache)
    await db.commit()

    return AIAnalyzeResponse(
        content=content,
        cached_at=now.isoformat(),
        expires_at=expires_at.isoformat(),
        warning=warning,
    )
