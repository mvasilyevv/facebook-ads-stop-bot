# -*- coding: utf-8 -*-
"""FastAPI роутер для настроек observer (settings_observer).

Endpoints под /api (благодаря auto-discovery с prefix="/api"):
- GET  /settings/observer          — читает ObserverConfig singleton
- PUT  /settings/observer          — обновляет все поля
- PATCH /settings/observer/scanning     — переключает is_scanning_enabled
- PATCH /settings/observer/auto-enable  — переключает auto_enable_recommendations
- POST /settings/observer/scan-now — публикует Redis сигнал fb_agent:observer:trigger
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import DepEngine, DepRedis, DepSettings
from apps.api.routers.v1.schemas.settings_observer import (
    ActViaApiToggleRequest,
    AutoEnableToggleRequest,
    CampaignAllowlistRequest,
    CampaignOption,
    ObserverSettingsPutRequest,
    ObserverSettingsResponse,
    ScanningToggleRequest,
    ScanNowResponse,
)
from core.models.settings.observer_config import ObserverConfig
from core.observer.queries import campaign_matches_owner

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings/observer", tags=["settings"])

# Канал Redis для триггера scan-now.
_SCAN_NOW_CHANNEL = "fb_agent:observer:trigger"


async def _get_singleton(session: AsyncSession) -> ObserverConfig:
    """Возвращает singleton ObserverConfig, создаёт строку с дефолтами если её нет."""
    row = await session.scalar(
        select(ObserverConfig).where(ObserverConfig.singleton_key == "default")
    )
    if row is None:
        # Создаём запись с server-defaults (INSERT ... ON CONFLICT тоже работал бы,
        # но для singleton достаточно простого INSERT — race condition при первом запуске
        # маловероятен, а повторный CREATE бросит IntegrityError, которая rollback'нется
        # и в следующем запросе row будет найдена).
        row = ObserverConfig()
        session.add(row)
        await session.flush()
        await session.refresh(row)
    return row


def _to_response(cfg: ObserverConfig) -> ObserverSettingsResponse:
    """Снимок ObserverConfig в response-схему. Читать ДО commit (иначе expired-атрибуты)."""
    return ObserverSettingsResponse(
        is_scanning_enabled=cfg.is_scanning_enabled,
        default_interval_seconds=cfg.interval_seconds,
        auto_enable_recommendations=cfg.auto_enable_recommendations,
        owner_campaign_tag=cfg.owner_campaign_tag,
        act_via_api=cfg.act_via_api,
        campaign_ids=list(cfg.campaign_ids or []),
    )


@router.get("", response_model=ObserverSettingsResponse)
async def get_observer_settings(engine: DepEngine) -> ObserverSettingsResponse:
    """Возвращает текущий ObserverConfig singleton.

    Поля warning_percent_of_stop и WARNING-параметры возвращаются как null —
    они перенесены в OfferRule (per-offer). Фронт получает стабильный shape.
    """
    async with AsyncSession(engine) as session:
        cfg = await _get_singleton(session)
        return _to_response(cfg)


@router.put("", response_model=ObserverSettingsResponse)
async def put_observer_settings(
    body: ObserverSettingsPutRequest,
    engine: DepEngine,
) -> ObserverSettingsResponse:
    """Обновляет все поля ObserverConfig singleton.

    Валидация: default_interval_seconds от 30 до 600 (через Pydantic Field).
    """
    async with AsyncSession(engine) as session:
        cfg = await _get_singleton(session)
        cfg.is_scanning_enabled = body.is_scanning_enabled
        cfg.interval_seconds = body.default_interval_seconds
        cfg.auto_enable_recommendations = body.auto_enable_recommendations
        cfg.owner_campaign_tag = body.owner_campaign_tag
        # act_via_api — money-критичный флаг: трогаем только если клиент явно прислал
        # значение (None = не менять, защита от сброса старыми клиентами без поля).
        if body.act_via_api is not None:
            cfg.act_via_api = body.act_via_api
        # campaign_ids: None = не менять, [] = очистить.
        if body.campaign_ids is not None:
            cfg.campaign_ids = list(body.campaign_ids)
        # Считываем значения ДО commit — после commit SQLAlchemy помечает
        # атрибуты expired, и их чтение триггерит lazy-load вне greenlet.
        result = _to_response(cfg)
        await session.commit()
        return result


@router.patch("/scanning", response_model=ObserverSettingsResponse)
async def patch_observer_scanning(
    body: ScanningToggleRequest,
    engine: DepEngine,
) -> ObserverSettingsResponse:
    """Переключает только is_scanning_enabled, не трогая остальные поля."""
    async with AsyncSession(engine) as session:
        cfg = await _get_singleton(session)
        cfg.is_scanning_enabled = body.enabled
        # Остальные поля не менялись — читаем из in-memory состояния до commit.
        result = _to_response(cfg)
        await session.commit()
        return result


@router.patch("/auto-enable", response_model=ObserverSettingsResponse)
async def patch_observer_auto_enable(
    body: AutoEnableToggleRequest,
    engine: DepEngine,
) -> ObserverSettingsResponse:
    """Переключает только auto_enable_recommendations (требует миграции 0003)."""
    async with AsyncSession(engine) as session:
        cfg = await _get_singleton(session)
        cfg.auto_enable_recommendations = body.enabled
        result = _to_response(cfg)
        await session.commit()
        return result


@router.patch("/act-via-api", response_model=ObserverSettingsResponse)
async def patch_observer_act_via_api(
    body: ActViaApiToggleRequest,
    engine: DepEngine,
) -> ObserverSettingsResponse:
    """Переключает только act_via_api — канал исполнения toggle-действий.

    True → авто-стоп observer'а и ручные кнопки идут через Marketing API
    (pause_ad/activate_ad). False → DOM-клик через browser-agent. Требует
    запущенного meta_api_worker при True.
    """
    async with AsyncSession(engine) as session:
        cfg = await _get_singleton(session)
        cfg.act_via_api = body.enabled
        result = _to_response(cfg)
        await session.commit()
        return result


@router.patch("/campaigns", response_model=ObserverSettingsResponse)
async def patch_observer_campaigns(
    body: CampaignAllowlistRequest,
    engine: DepEngine,
) -> ObserverSettingsResponse:
    """Задаёт allowlist кампаний для am-режима (#3).

    Фильтрует am_tabular по campaign.id IN [...]: в общем кабинете не тянем чужие ад'ы.
    Пустой список — без фильтра по кампаниям (owner_campaign_tag всё равно отсекает чужое).
    """
    async with AsyncSession(engine) as session:
        cfg = await _get_singleton(session)
        cfg.campaign_ids = list(body.campaign_ids)
        result = _to_response(cfg)
        await session.commit()
        return result


@router.get("/campaigns", response_model=list[CampaignOption])
async def list_observer_campaigns(engine: DepEngine) -> list[CampaignOption]:
    """Список кампаний (накопленных observer'ом) для выбора allowlist (#3).

    Фильтр по owner_campaign_tag (word-boundary, через campaign_matches_owner).
    selected — входит ли кампания в текущий allowlist (cfg.campaign_ids).
    Кампании без Meta fb_campaign_id пропускаются — их нельзя заскоупить по campaign.id.
    """
    async with AsyncSession(engine) as session:
        cfg = await _get_singleton(session)
        allowlist = set(cfg.campaign_ids or [])
        owner_tag = cfg.owner_campaign_tag
        rows = (
            await session.execute(
                text(
                    """
                    SELECT fb_campaign_id, campaign_name
                    FROM fb_campaigns
                    WHERE fb_campaign_id IS NOT NULL AND is_active = true
                    ORDER BY last_seen_at DESC NULLS LAST
                    LIMIT 500
                    """
                )
            )
        ).all()

    out: list[CampaignOption] = []
    for cid, name in rows:
        if not campaign_matches_owner(campaign_name=name or "", ad_name="", owner_tag=owner_tag):
            continue
        out.append(CampaignOption(id=str(cid), name=name or "", selected=str(cid) in allowlist))
    return out


@router.post("/campaigns/refresh", response_model=list[CampaignOption])
async def refresh_observer_campaigns(
    engine: DepEngine, settings: DepSettings
) -> list[CampaignOption]:
    """Live-обновление списка кампаний через browser-agent (Graph API, МИМО allowlist).

    Решает «замкнутый круг»: обычный скан с allowlist не подхватывает новые кампании,
    поэтому их нельзя выбрать. Здесь резолвим ВСЕ кампании владельца по owner_tag живьём,
    апсертим в fb_campaigns (чтобы GET /campaigns их видел) и возвращаем обновлённый список.
    503 при недоступности browser-agent.
    """
    import grpc

    from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig
    from core.crypto import decrypt
    from core.models.settings.vision_config import VisionConfig

    async with AsyncSession(engine) as session:
        cfg = await _get_singleton(session)
        owner_tag = cfg.owner_campaign_tag
        allowlist = set(cfg.campaign_ids or [])
        vc = await session.scalar(
            select(VisionConfig).where(VisionConfig.singleton_key == "default")
        )
        x_token = settings.vision_x_token
        profile_id = settings.vision_profile_id
        if vc:
            if vc.x_token_encrypted:
                try:
                    x_token = decrypt(vc.x_token_encrypted)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("refresh_campaigns: decrypt vision token failed: %s", exc)
            if vc.profile_id:
                profile_id = vc.profile_id

    client = BrowserAgentClient(
        BrowserAgentConfig(
            vision_x_token=x_token,
            vision_api_url=settings.vision_api_url,
            vision_profile_id=profile_id,
        )
    )
    try:
        await client.start()
        # НЕ создаём новую сессию: browser-agent сам возьмёт активную ads-сессию observer'а
        # (getPreferredSession) с кешированным graph-токеном — иначе токен не извлекался.
        campaigns = await client.list_campaigns(owner_tag=owner_tag or "")
    except grpc.RpcError as exc:
        raise HTTPException(status_code=503, detail=f"browser-agent недоступен: {exc}") from exc
    except Exception as exc:
        raise HTTPException(status_code=503, detail=f"Ошибка резолва кампаний: {exc}") from exc
    finally:
        try:
            await client.close()
        except Exception:  # noqa: BLE001
            pass

    # Апсерт в каталог (ON CONFLICT по campaign_name, как в writers) — чтобы GET /campaigns видел новые.
    now = datetime.now(UTC)
    if campaigns:
        async with AsyncSession(engine) as session:
            for c in campaigns:
                await session.execute(
                    text(
                        """
                        INSERT INTO fb_campaigns (fb_campaign_id, campaign_name, last_seen_at)
                        VALUES (:cid, :name, :now)
                        ON CONFLICT (campaign_name) DO UPDATE
                        SET last_seen_at = :now,
                            fb_campaign_id =
                                COALESCE(EXCLUDED.fb_campaign_id, fb_campaigns.fb_campaign_id),
                            is_active = TRUE
                        """
                    ),
                    {"cid": c["id"], "name": c["name"], "now": now},
                )
            await session.commit()

    result: list[CampaignOption] = []
    for c in campaigns:
        if not campaign_matches_owner(campaign_name=c["name"], ad_name="", owner_tag=owner_tag):
            continue
        result.append(
            CampaignOption(id=c["id"], name=c["name"], selected=c["id"] in allowlist)
        )
    return result


@router.post("/scan-now", response_model=ScanNowResponse)
async def post_scan_now(redis: DepRedis) -> ScanNowResponse:
    """Публикует Redis-событие fb_agent:observer:trigger для немедленного запуска scan.

    observer_worker подписан на канал fb_agent:observer:trigger и немедленно
    запускает scan-цикл по этому событию (main.py::_on_trigger).
    Если Redis недоступен — возвращает 503.
    """
    payload = f'{{"requested_by": "api", "ts": "{datetime.now(UTC).isoformat()}"}}'
    try:
        await redis.publish(_SCAN_NOW_CHANNEL, payload)
    except Exception as exc:
        logger.error("Не удалось опубликовать событие scan-now в Redis: %s", exc)
        raise HTTPException(status_code=503, detail="Redis недоступен") from exc
    return ScanNowResponse(status="triggered")
