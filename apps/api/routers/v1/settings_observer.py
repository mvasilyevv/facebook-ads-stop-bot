# -*- coding: utf-8 -*-
"""FastAPI роутер для настроек observer (settings_observer).

Endpoints под /api (благодаря auto-discovery с prefix="/api"):
- GET  /settings/observer          — читает ObserverConfig singleton
- PATCH /settings/observer/interval     — точечно меняет интервал
- PATCH /settings/observer/scanning     — переключает is_scanning_enabled
- PATCH /settings/observer/owner-tag    — точечно меняет owner_campaign_tag (анти лост-апдейт)
- PATCH /settings/observer/ads-manager-columns — меняет presentation-колонки вкладки
- POST /settings/observer/scan-now — ставит durable observer_scan в PostgreSQL
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, HTTPException, Query, Response, status
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import DepEngine, DepSettings
from apps.api.routers.v1.schemas.settings_observer import (
    AdsManagerColumnOption,
    AdsManagerColumnsPatchRequest,
    CampaignAllowlistRequest,
    CampaignOption,
    ObserverIntervalPatchRequest,
    ObserverSettingsResponse,
    OwnerTagPatchRequest,
    ScanningToggleRequest,
    ScanNowResponse,
)
from core.commands.service import CommandService
from core.models.settings.observer_config import ObserverConfig
from core.observer.am_columns import (
    KNOWN_AM_COLUMN_OPTIONS,
    build_am_columns_qs,
    selected_am_columns,
)
from core.observer.queries import campaign_matches_owner
from core.observer.scan_tasks import observer_scan_idempotency_key
from core.tasks.browser_fence import (
    BrowserExclusiveMaintenance,
    BrowserFenceLeaseLost,
    BrowserOperationBlocked,
    BrowserOperationDrainTimeout,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/settings/observer", tags=["settings"])

# Дата в названии кампании (DD.MM или DD.MM.YY). У owner-кампаний она в КОНЦЕ имени
# («MV | GH_CR | video | adset.pro | 18.06»), поэтому берём ПОСЛЕДНее совпадение.
_NAME_DATE_RE = re.compile(r"(\d{1,2})\.(\d{1,2})(?:\.(\d{2,4}))?")


# Горизонт свежести списка кампаний: старше N дней (по дате из названия) — прячем,
# если кампания не выбрана в allowlist. Дата в имени = день запуска (нейминг-SOP),
# кампании живут днями — старьё в списке только мешает выбирать.
CAMPAIGN_LIST_HORIZON_DAYS = int(os.environ.get("CAMPAIGN_LIST_HORIZON_DAYS", "14"))


def _campaign_name_date(name: str, *, today: date) -> date | None:
    """Дата запуска из названия кампании (последний DD.MM[.YY]) или None.

    Год в имени обычно опущен («28.06»): берём текущий, а если дата получается
    в будущем дальше чем на 2 дня (нейминг today+1 допускает завтра) — значит
    это прошлый год (декабрьские имена в январе). Невалидный день/месяц → None.
    """
    matches = _NAME_DATE_RE.findall(name or "")
    if not matches:
        return None
    d, m, y = matches[-1]
    try:
        if y:
            year = int(y) + 2000 if len(y) == 2 else int(y)
            return date(year, int(m), int(d))
        candidate = date(today.year, int(m), int(d))
    except ValueError:
        return None
    if (candidate - today).days > 2:
        candidate = candidate.replace(year=today.year - 1)
    return candidate


def _is_stale_campaign(name: str, *, today: date) -> bool:
    """True — дата в имени старше CAMPAIGN_LIST_HORIZON_DAYS. Без даты → False (не прячем)."""
    launched = _campaign_name_date(name, today=today)
    if launched is None:
        return False
    return (today - launched).days > CAMPAIGN_LIST_HORIZON_DAYS


def _filter_stale_options(options: list[CampaignOption]) -> list[CampaignOption]:
    """Прячет старые невыбранные кампании (выбранные показываем всегда — их снимают тут же)."""
    today = datetime.now(UTC).date()
    return [o for o in options if o.selected or not _is_stale_campaign(o.name, today=today)]


def _campaign_sort_key(name: str) -> tuple[int, int, int, str]:
    """Ключ сортировки кампаний по дате из названия (свежие выше при reverse=True).

    Парсим последнюю дату DD.MM[.YY] в имени → (year, month, day, name). Без даты →
    (0,0,0) — уезжает в конец. Имя хвостом для стабильного порядка при равной дате.
    Сортировка по ИМЕНИ лексически не годится: дата в конце, префикс (GH_CR/GH) доминирует.
    """
    matches = _NAME_DATE_RE.findall(name or "")
    if matches:
        d, m, y = matches[-1]
        year = (int(y) + 2000 if len(y) == 2 else int(y)) if y else 0
        return (year, int(m), int(d), name or "")
    return (0, 0, 0, name or "")


async def _get_singleton(session: AsyncSession) -> ObserverConfig:
    """Возвращает singleton ObserverConfig, создаёт строку с дефолтами если её нет."""
    row = await session.scalar(
        select(ObserverConfig).where(ObserverConfig.singleton_key == "default")
    )
    if row is None:
        # Создаём запись с server-defaults (INSERT ... ON CONFLICT тоже работал бы,
        # но для singleton достаточно простого INSERT — race condition при первом запуске
        # маловероятен; при гонке повторный запрос увидит уже созданную строку
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
        owner_campaign_tag=cfg.owner_campaign_tag,
        campaign_ids=list(cfg.campaign_ids or []),
        am_columns=list(selected_am_columns(cfg.am_columns_qs)),
        am_columns_use_default=not bool((cfg.am_columns_qs or "").strip()),
        am_column_options=[
            AdsManagerColumnOption(id=column_id, label=label)
            for column_id, label in KNOWN_AM_COLUMN_OPTIONS
        ],
    )


@router.get("", response_model=ObserverSettingsResponse)
async def get_observer_settings(engine: DepEngine) -> ObserverSettingsResponse:
    """Возвращает текущий ObserverConfig singleton."""
    async with AsyncSession(engine) as session:
        cfg = await _get_singleton(session)
        return _to_response(cfg)


@router.patch("/interval", response_model=ObserverSettingsResponse)
async def patch_observer_interval(
    body: ObserverIntervalPatchRequest,
    engine: DepEngine,
) -> ObserverSettingsResponse:
    """Меняет только интервал, не перезаписывая scanning/tag/allowlist."""
    async with AsyncSession(engine) as session:
        cfg = await _get_singleton(session)
        cfg.interval_seconds = body.default_interval_seconds
        result = _to_response(cfg)
        await session.commit()
        return result


@router.patch("/scanning", response_model=ObserverSettingsResponse)
async def patch_observer_scanning(
    body: ScanningToggleRequest,
    engine: DepEngine,
) -> ObserverSettingsResponse:
    """Переключает только is_scanning_enabled, не трогая остальные поля.

    Гейт включения: не даём включить скан, если мониторить нечего (пустой allowlist при
    одном кабинете / нет активных офферов). Иначе скан крутился бы вхолостую раз в интервал,
    ничего не отслеживая. Возвращаем 409 с понятной причиной — фронт показывает её на клике
    «Включить», а заполнять кампании пользователь идёт на страницу «Кампании».
    """
    async with AsyncSession(engine) as session:
        cfg = await _get_singleton(session)
        if body.enabled:
            from core.observer.accounts import scan_nothing_monitored_reason

            reason = await scan_nothing_monitored_reason(engine, list(cfg.campaign_ids or []))
            if reason:
                raise HTTPException(status_code=409, detail=reason)
        cfg.is_scanning_enabled = body.enabled
        # Остальные поля не менялись — читаем из in-memory состояния до commit.
        result = _to_response(cfg)
        await session.commit()
        return result


@router.patch("/owner-tag", response_model=ObserverSettingsResponse)
async def patch_observer_owner_tag(
    body: OwnerTagPatchRequest,
    engine: DepEngine,
) -> ObserverSettingsResponse:
    """Меняет только owner_campaign_tag, не трогая остальные поля.

    Точечный PATCH против лост-апдейта (аудит 2026-07-12, C-1): фронты сохраняли тег
    через full-PUT из закэшированного состояния и молча откатывали is_scanning_enabled.
    """
    async with AsyncSession(engine) as session:
        cfg = await _get_singleton(session)
        cfg.owner_campaign_tag = (body.owner_campaign_tag or "").strip() or None
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


@router.patch("/ads-manager-columns", response_model=ObserverSettingsResponse)
async def patch_ads_manager_columns(
    body: AdsManagerColumnsPatchRequest,
    engine: DepEngine,
) -> ObserverSettingsResponse:
    """Меняет только presentation-колонки видимой вкладки Ads Manager.

    Сервер принимает известные IDs и сам строит query. NULL/пустой список хранится
    как NULL, чтобы browser-agent использовал прежний fallback env → default.
    """
    async with AsyncSession(engine) as session:
        cfg = await _get_singleton(session)
        cfg.am_columns_qs = build_am_columns_qs(body.column_ids)
        result = _to_response(cfg)
        await session.commit()
        return result


@router.get("/campaigns", response_model=list[CampaignOption])
async def list_observer_campaigns(
    engine: DepEngine,
    include_stale: bool = Query(
        default=False,
        description=f"Показать и старые кампании (дата в имени старше "
        f"{CAMPAIGN_LIST_HORIZON_DAYS} дней). По умолчанию старьё скрыто, "
        "кроме выбранных в allowlist.",
    ),
) -> list[CampaignOption]:
    """Список кампаний (накопленных observer'ом) для выбора allowlist (#3).

    Фильтр по owner_campaign_tag (word-boundary, через campaign_matches_owner).
    selected — входит ли кампания в текущий allowlist (cfg.campaign_ids).
    Кампании без Meta fb_campaign_id пропускаются — их нельзя заскоупить по campaign.id.
    Свежесть: по умолчанию кампании с датой в имени старше CAMPAIGN_LIST_HORIZON_DAYS
    скрываются (если не выбраны) — see include_stale.
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
    if not include_stale:
        out = _filter_stale_options(out)
    # Сортировка по дате из названия — свежие кампании выше.
    out.sort(key=lambda o: _campaign_sort_key(o.name), reverse=True)
    return out


@router.post("/campaigns/refresh", response_model=list[CampaignOption])
async def refresh_observer_campaigns(
    engine: DepEngine,
    settings: DepSettings,
    ad_account_id: str | None = Query(
        default=None,
        description="Числовой ID кабинета. Если не задан, используются кабинеты активных офферов.",
    ),
    include_stale: bool = Query(
        default=False, description="Показать и старые кампании (как в GET /campaigns)."
    ),
) -> list[CampaignOption]:
    """Run discovery under an exclusive fence because StartBrowser may restart."""
    try:
        async with BrowserExclusiveMaintenance(
            engine,
            operation_kind="campaign_refresh",
        ) as fence:
            result = await _refresh_observer_campaigns_unfenced(
                engine=engine,
                settings=settings,
                ad_account_id=ad_account_id,
                include_stale=include_stale,
            )
            await fence.assert_held()
            return result
    except BrowserOperationBlocked as exc:
        raise HTTPException(
            status_code=409,
            detail="Vision maintenance is active; campaign refresh was not started",
        ) from exc
    except BrowserOperationDrainTimeout as exc:
        raise HTTPException(
            status_code=409,
            detail="Active browser work did not drain; campaign refresh was not started",
        ) from exc
    except BrowserFenceLeaseLost as exc:
        raise HTTPException(
            status_code=503,
            detail="Campaign refresh fence was lost; retry after reconciliation",
        ) from exc


async def _refresh_observer_campaigns_unfenced(
    *,
    engine,
    settings,
    ad_account_id: str | None,
    include_stale: bool,
) -> list[CampaignOption]:
    """Live-обновление списка кампаний через browser-agent (Graph API, МИМО allowlist).

    Решает «замкнутый круг»: обычный скан с allowlist не подхватывает новые кампании,
    поэтому их нельзя выбрать. Здесь резолвим кампании владельца по owner_tag живьём,
    апсертим в fb_campaigns (чтобы GET /campaigns их видел) и возвращаем обновлённый список.
    503 при недоступности browser-agent. Результат отсортирован по имени убыванием
    (в названии есть дата → свежие выше).

    Кабинеты: если задан явный ad_account_id — только он; иначе ВСЕ кабинеты активных
    офферов (offer_ad_accounts, resolve_scan_account_ids). Без настроенного кабинета
    запрос отклоняется: текущая browser-вкладка не является identity.
    """
    import grpc

    from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig
    from core.meta_api.identity import require_ad_account_id
    from core.vision_runtime import VisionConfigurationError, load_vision_runtime_config

    async with AsyncSession(engine) as session:
        cfg = await _get_singleton(session)
        owner_tag = cfg.owner_campaign_tag
        allowlist = set(cfg.campaign_ids or [])

    try:
        vision = await load_vision_runtime_config(engine)
    except VisionConfigurationError as exc:
        raise HTTPException(status_code=409, detail="Vision runtime не настроен") from exc

    # Кабинеты для обхода: явный ad_account_id (один) ИЛИ ВСЕ кабинеты активных офферов.
    from core.observer.accounts import resolve_scan_account_ids

    if ad_account_id:
        targets = [require_ad_account_id(ad_account_id)]
    else:
        targets = [require_ad_account_id(value) for value in await resolve_scan_account_ids(engine)]
    if not targets:
        raise HTTPException(
            status_code=409,
            detail="Нет явно настроенного ad_account_id для обновления кампаний",
        )

    client = BrowserAgentClient(
        BrowserAgentConfig(
            vision_x_token=vision.x_token,
            vision_api_url=settings.vision_api_url,
            vision_profile_id=vision.profile_id,
            vision_folder_id=vision.folder_id,
            # grpc_host/port из env — иначе из Docker api клиент идёт на localhost:50051,
            # а browser-agent на хосте (host.docker.internal). Это и была причина «пусто»:
            # refresh не достукивался до browser-agent. Зеркало settings_vision/observer.
            grpc_host=os.environ.get("BROWSER_AGENT_HOST", "localhost"),
            grpc_port=int(os.environ.get("BROWSER_AGENT_GRPC_PORT", "50051")),
        )
    )
    merged: dict[str, dict[str, str]] = {}
    try:
        await client.start()
        # Гарантируем активную Vision-сессию: refresh самодостаточен и НЕ зависит от того,
        # сканирует ли observer сейчас (иначе зацикленность: включение скана гейтится пустым
        # allowlist'ом → observer не сканирует → нет сессии → refresh не видит кампании →
        # нечем заполнить allowlist). StartBrowser создаёт только process-local сессию,
        # подключаясь к уже живому CDP; lifecycle профиля здесь не меняется.
        await client.start_browser()
        # По каждому кабинету list_campaigns откроет его вкладку (ensureAdsManagerPage(actId)),
        # достанет graph-токен со страницы и резолвит кампании по owner_tag.
        for acc in targets:
            cs = await client.list_campaigns(owner_tag=owner_tag or "", ad_account_id=acc)
            for c in cs:
                merged[c["id"]] = {**c, "ad_account_id": acc}
    except grpc.RpcError as exc:
        raise HTTPException(status_code=503, detail="browser-agent недоступен") from exc
    except Exception as exc:
        # LOW (аудит 02.07): голый Exception — не показываем str(exc) клиенту (может
        # нести внутренние детали), полная ошибка уходит в лог.
        logger.exception("Ошибка резолва кампаний через browser-agent")
        raise HTTPException(
            status_code=503, detail="Ошибка резолва кампаний — подробности в логе сервера"
        ) from exc
    finally:
        try:
            await client.close()
        except Exception:  # noqa: BLE001
            pass

    campaigns = list(merged.values())

    # Апсерт в каталог по fb_campaign_id, чтобы GET /campaigns видел новые.
    # У campaigns-edge ID есть всегда.
    now = datetime.now(UTC)
    if campaigns:
        async with AsyncSession(engine) as session:
            for c in campaigns:
                await session.execute(
                    text(
                        """
                        INSERT INTO fb_campaigns
                            (fb_campaign_id, campaign_name, ad_account_id, last_seen_at)
                        VALUES (:cid, :name, :account_id, :now)
                        ON CONFLICT (fb_campaign_id) WHERE fb_campaign_id IS NOT NULL
                        DO UPDATE
                        SET last_seen_at = :now,
                            campaign_name = EXCLUDED.campaign_name,
                            ad_account_id = EXCLUDED.ad_account_id,
                            is_active = TRUE
                        """
                    ),
                    {
                        "cid": c["id"],
                        "name": c["name"],
                        "account_id": c["ad_account_id"],
                        "now": now,
                    },
                )
            await session.commit()

    result: list[CampaignOption] = []
    for c in campaigns:
        if not campaign_matches_owner(campaign_name=c["name"], ad_name="", owner_tag=owner_tag):
            continue
        result.append(CampaignOption(id=c["id"], name=c["name"], selected=c["id"] in allowlist))
    if not include_stale:
        # Апсерт выше сохранил ВСЁ в каталог; прячем старьё только из ответа.
        result = _filter_stale_options(result)
    # Сортировка по дате из названия — свежие кампании выше.
    result.sort(key=lambda o: _campaign_sort_key(o.name), reverse=True)
    return result


@router.post(
    "/scan-now",
    response_model=ScanNowResponse,
    status_code=202,
    responses={
        200: {
            "model": ScanNowResponse,
            "description": "Existing scan command lifecycle state",
        }
    },
)
async def post_scan_now(engine: DepEngine, response: Response) -> ScanNowResponse:
    """Atomically enqueue a scan; ``202`` means queued, never completed."""
    request_nonce = uuid.uuid4().hex
    receipt = await CommandService(engine).enqueue_scan_retry(
        requested_by="operator_api",
        idempotency_key=observer_scan_idempotency_key("api", request_nonce),
        reason="operator_scan_now",
    )
    response.status_code = (
        status.HTTP_202_ACCEPTED if receipt.state == "queued" else status.HTTP_200_OK
    )
    return ScanNowResponse(
        status=receipt.state,
        task_id=receipt.task_id,
        correlation_id=receipt.correlation_id,
        created=receipt.created,
    )
