# -*- coding: utf-8 -*-
"""FastAPI роутер для настроек observer (settings_observer).

Endpoints под /api (благодаря auto-discovery с prefix="/api"):
- GET  /settings/observer          — читает ObserverConfig singleton
- PUT  /settings/observer          — обновляет все поля (гейт скана как в PATCH /scanning)
- PATCH /settings/observer/scanning     — переключает is_scanning_enabled
- PATCH /settings/observer/owner-tag    — точечно меняет owner_campaign_tag (анти лост-апдейт)
- PATCH /settings/observer/auto-enable  — переключает auto_enable_recommendations
- GET/POST/DELETE /settings/observer/auto-enable-exclusions — исключения по объявлениям
- POST /settings/observer/scan-now — ставит durable observer_scan в PostgreSQL
"""

from __future__ import annotations

import logging
import os
import re
import uuid
from datetime import UTC, date, datetime

from fastapi import APIRouter, HTTPException, Query, Response
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from apps.api.deps import DepEngine, DepSettings
from apps.api.routers.v1.schemas.settings_observer import (
    AutoEnableExclusionCreate,
    AutoEnableExclusionResponse,
    AutoEnableToggleRequest,
    CampaignAllowlistRequest,
    CampaignOption,
    ObserverSettingsPutRequest,
    ObserverSettingsResponse,
    OwnerTagPatchRequest,
    ScanningToggleRequest,
    ScanNowResponse,
)
from core.models.catalog.fb_ad import FbAd
from core.models.observer.ad_auto_enable_disabled import AdAutoEnableDisabled
from core.models.settings.observer_config import ObserverConfig
from core.observer.queries import campaign_matches_owner
from core.observer.scan_tasks import enqueue_observer_scan, observer_scan_idempotency_key
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
        campaign_ids=list(cfg.campaign_ids or []),
    )


@router.get("", response_model=ObserverSettingsResponse)
async def get_observer_settings(engine: DepEngine) -> ObserverSettingsResponse:
    """Возвращает текущий ObserverConfig singleton."""
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
    Гейт «нечего сканировать» (аудит 2026-07-12, C-1): PUT с is_scanning_enabled=true
    проходит ту же проверку, что PATCH /scanning — иначе full-PUT включал скан
    в обход гейта при пустом allowlist («всё зелёное, авто-стоп не работает»).
    """
    async with AsyncSession(engine) as session:
        cfg = await _get_singleton(session)
        if body.is_scanning_enabled:
            from core.observer.accounts import scan_nothing_monitored_reason

            # Гейт проверяет allowlist ИЗ ЭТОГО ЖЕ тела (если прислан), иначе текущий.
            effective_allowlist = (
                list(body.campaign_ids)
                if body.campaign_ids is not None
                else list(cfg.campaign_ids or [])
            )
            reason = await scan_nothing_monitored_reason(engine, effective_allowlist)
            if reason:
                raise HTTPException(status_code=409, detail=reason)
        cfg.is_scanning_enabled = body.is_scanning_enabled
        cfg.interval_seconds = body.default_interval_seconds
        cfg.auto_enable_recommendations = body.auto_enable_recommendations
        cfg.owner_campaign_tag = body.owner_campaign_tag
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


@router.patch("/auto-enable", response_model=ObserverSettingsResponse)
async def patch_observer_auto_enable(
    body: AutoEnableToggleRequest,
    engine: DepEngine,
) -> ObserverSettingsResponse:
    """Переключает только auto_enable_recommendations."""
    async with AsyncSession(engine) as session:
        cfg = await _get_singleton(session)
        cfg.auto_enable_recommendations = body.enabled
        result = _to_response(cfg)
        await session.commit()
        return result


@router.get(
    "/auto-enable-exclusions",
    response_model=list[AutoEnableExclusionResponse],
)
async def list_auto_enable_exclusions(
    engine: DepEngine,
) -> list[AutoEnableExclusionResponse]:
    """List ads which the operator excluded from automatic re-enable."""
    stmt = (
        select(
            AdAutoEnableDisabled.ad_id,
            AdAutoEnableDisabled.created_at,
            AdAutoEnableDisabled.reason,
            FbAd.fb_ad_id,
            FbAd.ad_name,
        )
        .join(FbAd, AdAutoEnableDisabled.ad_id == FbAd.id, isouter=True)
        .order_by(AdAutoEnableDisabled.created_at.desc())
    )
    async with engine.connect() as conn:
        rows = (await conn.execute(stmt)).fetchall()
    return [
        AutoEnableExclusionResponse(
            fb_ad_id=row.fb_ad_id or "",
            internal_id=row.ad_id,
            ad_name=row.ad_name,
            disabled_at=row.created_at,
            reason=row.reason,
        )
        for row in rows
    ]


@router.post(
    "/auto-enable-exclusions/{fb_ad_id}",
    response_model=AutoEnableExclusionResponse,
    status_code=201,
)
async def create_auto_enable_exclusion(
    fb_ad_id: str,
    engine: DepEngine,
    body: AutoEnableExclusionCreate | None = None,
) -> AutoEnableExclusionResponse:
    """Exclude one existing ad from automatic re-enable."""
    reason = body.reason if body else None
    async with engine.begin() as conn:
        ad_row = (
            await conn.execute(select(FbAd.id, FbAd.ad_name).where(FbAd.fb_ad_id == fb_ad_id))
        ).one_or_none()
        if ad_row is None:
            raise HTTPException(status_code=404, detail=f"Объявление {fb_ad_id!r} не найдено")
        if (
            await conn.execute(
                select(AdAutoEnableDisabled.id).where(AdAutoEnableDisabled.ad_id == ad_row.id)
            )
        ).one_or_none():
            raise HTTPException(
                status_code=409,
                detail=f"Авто-включение уже отключено для объявления {fb_ad_id!r}",
            )

        now = datetime.now(UTC)
        try:
            result = (
                await conn.execute(
                    AdAutoEnableDisabled.__table__.insert()
                    .values(
                        ad_id=ad_row.id,
                        cabinet_day_started_at=now,
                        reason=reason,
                        created_at=now,
                    )
                    .returning(
                        AdAutoEnableDisabled.ad_id,
                        AdAutoEnableDisabled.created_at,
                        AdAutoEnableDisabled.reason,
                    )
                )
            ).one()
        except IntegrityError as exc:
            raise HTTPException(
                status_code=409,
                detail=f"Авто-включение уже отключено для объявления {fb_ad_id!r}",
            ) from exc

    return AutoEnableExclusionResponse(
        fb_ad_id=fb_ad_id,
        internal_id=result.ad_id,
        ad_name=ad_row.ad_name,
        disabled_at=result.created_at,
        reason=result.reason,
    )


@router.delete("/auto-enable-exclusions/{fb_ad_id}", status_code=204)
async def remove_auto_enable_exclusion(fb_ad_id: str, engine: DepEngine) -> Response:
    """Remove one automatic re-enable exclusion."""
    async with engine.begin() as conn:
        ad_row = (
            await conn.execute(select(FbAd.id).where(FbAd.fb_ad_id == fb_ad_id))
        ).one_or_none()
        if ad_row is None:
            raise HTTPException(status_code=404, detail=f"Объявление {fb_ad_id!r} не найдено")
        deleted = (
            await conn.execute(
                delete(AdAutoEnableDisabled)
                .where(AdAutoEnableDisabled.ad_id == ad_row.id)
                .returning(AdAutoEnableDisabled.id)
            )
        ).one_or_none()
    if deleted is None:
        raise HTTPException(
            status_code=404,
            detail=f"Флаг авто-включения не установлен для объявления {fb_ad_id!r}",
        )
    return Response(status_code=204)


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
    офферов (offers.ad_account_ids, resolve_scan_account_ids). Без настроенного кабинета
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
            vision_folder_id=os.environ.get("VISION_FOLDER_ID") or None,
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


@router.post("/scan-now", response_model=ScanNowResponse, status_code=202)
async def post_scan_now(engine: DepEngine) -> ScanNowResponse:
    """Atomically enqueue a scan; ``202`` means queued, never completed."""
    request_nonce = uuid.uuid4().hex
    receipt = await enqueue_observer_scan(
        engine,
        requested_by="operator_api",
        reason="operator_scan_now",
        idempotency_key=observer_scan_idempotency_key("api", request_nonce),
    )
    return ScanNowResponse(
        status="queued",
        task_id=receipt.task_id,
        correlation_id=receipt.correlation_id,
    )
