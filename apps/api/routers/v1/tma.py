# -*- coding: utf-8 -*-
"""Telegram Mini App: аутентификация + Bearer-guard (BL-15, Этап 0).

POST /api/tma/auth — принимает Telegram WebApp initData, валидирует по HMAC
(bot_token из telegram_config), сверяет user.id с telegram_recipients и выдаёт
подписанный сессионный токен (itsdangerous) + роль.

get_tma_principal — FastAPI-dependency: проверяет Bearer-токен на защищённых
TMA-endpoint'ах. Навешивается ТОЛЬКО на /tma/* (действия) — общие read-only
роутеры (/dashboard, /offers, ...) остаются открытыми, как для desktop-фронта.

Money/security: без валидного токена + активного recipient'а действия TMA
(disable/snooze/claim) недоступны.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from apps.api.deps import DepEngine, DepSettings
from apps.api.routers.v1.schemas.cabinet_autostart import (
    CabinetAutostartPutRequest,
    CabinetAutostartResponse,
)
from apps.api.routers.v1.schemas.tma import (
    TmaAdDetailResponse,
    TmaAdMetrics,
    TmaClaimResponse,
    TmaDisableRequest,
    TmaDisableResponse,
    TmaRecentAlert,
    TmaSnoozeRequest,
    TmaSnoozeResponse,
)
from core.auth.tma import (
    InvalidInitDataError,
    issue_session_token,
    validate_init_data,
    verify_session_token,
)
from core.config import Settings
from core.dashboard.snapshot import build_ad_snapshot
from core.meta_api.queue import create_mutation_task
from core.meta_api.schemas import MetaMutationPayload
from core.scheduler.cabinet_autostart import read_autostart_config, write_autostart_config
from core.telegram.service import find_recipient_by_telegram_user_id, load_telegram_config

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/tma", tags=["tma"])


class TmaAuthRequest(BaseModel):
    """Тело POST /tma/auth."""

    init_data: str = Field(..., description="Telegram WebApp initData (raw query string)")


class TmaAuthResponse(BaseModel):
    """Ответ авторизации: сессионный токен + роль recipient'а."""

    token: str
    role: str


class TmaMeResponse(BaseModel):
    """Кто я — для проверки сессии фронтом (под guard)."""

    telegram_user_id: int
    role: str


@dataclass(frozen=True)
class TmaPrincipal:
    """Авторизованный пользователь TMA (из проверенного Bearer-токена)."""

    telegram_user_id: int
    role: str
    chat_id: int

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"


def _tma_secret(settings: Settings) -> str:
    """Секрет подписи токена: tma_session_secret или фолбэк на encryption_key."""
    if settings.tma_session_secret:
        return settings.tma_session_secret.get_secret_value()
    return settings.encryption_key.get_secret_value()


@router.post("/auth", response_model=TmaAuthResponse)
async def tma_auth(
    body: TmaAuthRequest,
    engine: DepEngine,
    settings: DepSettings,
) -> TmaAuthResponse:
    """Валидирует initData и выдаёт сессионный токен + роль.

    503 — Telegram/secret не настроены; 401 — initData невалиден/истёк;
    403 — пользователь не в списке доступа (нет активного recipient'а).
    """
    cfg = await load_telegram_config(engine)
    if cfg is None or not cfg.bot_token:
        raise HTTPException(status_code=503, detail="Telegram-бот не настроен")

    secret = _tma_secret(settings)
    if not secret:
        raise HTTPException(status_code=503, detail="TMA auth не настроен (нет secret)")

    try:
        data = validate_init_data(body.init_data, cfg.bot_token)
    except InvalidInitDataError as exc:
        raise HTTPException(status_code=401, detail=f"initData невалиден: {exc}") from exc

    user = data.get("user") or {}
    uid = user.get("id")
    if not uid:
        raise HTTPException(status_code=401, detail="В initData нет user")

    recipient = await find_recipient_by_telegram_user_id(engine, telegram_user_id=int(uid))
    if recipient is None:
        raise HTTPException(status_code=403, detail="Нет доступа — получи invite у владельца бота")

    token = issue_session_token(str(uid), settings.tma_session_ttl_seconds, secret)
    logger.info("TMA auth: user_id=%s role=%s", uid, recipient.role)
    return TmaAuthResponse(token=token, role=recipient.role)


async def get_tma_principal(
    request: Request,
    engine: DepEngine,
    settings: DepSettings,
) -> TmaPrincipal:
    """Dependency: извлекает и проверяет Bearer-токен TMA. 401/403/503 при отказе.

    Перепроверяет recipient'а в БД на КАЖДОМ запросе (а не доверяет токену) —
    отзыв доступа (revoked_at) срабатывает немедленно, не дожидаясь истечения токена.
    """
    auth_header = request.headers.get("Authorization") or ""
    if not auth_header.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Нужен Bearer-токен")
    token = auth_header[len("Bearer ") :].strip()

    secret = _tma_secret(settings)
    if not secret:
        raise HTTPException(status_code=503, detail="TMA auth не настроен")

    try:
        payload = verify_session_token(token, secret, settings.tma_session_ttl_seconds)
    except InvalidInitDataError as exc:
        raise HTTPException(status_code=401, detail="Токен невалиден или истёк") from exc

    uid = int(payload.get("telegram_user_id", 0) or 0)
    recipient = await find_recipient_by_telegram_user_id(engine, telegram_user_id=uid)
    if recipient is None:
        raise HTTPException(status_code=403, detail="Доступ отозван")

    return TmaPrincipal(
        telegram_user_id=uid,
        role=recipient.role,
        chat_id=recipient.chat_id,
    )


DepTmaPrincipal = Annotated[TmaPrincipal, Depends(get_tma_principal)]


@router.get("/me", response_model=TmaMeResponse)
async def tma_me(principal: DepTmaPrincipal) -> TmaMeResponse:
    """Проверка сессии: возвращает текущего пользователя (под Bearer-guard)."""
    return TmaMeResponse(telegram_user_id=principal.telegram_user_id, role=principal.role)


# ===========================================================================
# Этап 2 — money-действия над объявлениями (все под DepTmaPrincipal)
# ===========================================================================


async def _load_ad_extras(
    engine: AsyncEngine, fb_ad_id: str
) -> tuple[list[TmaRecentAlert], str | None]:
    """История алертов (alert_events, 30 дней) + account_id (meta_api_observation).

    alert_events партиционирована по created_at — фильтр обязателен (pruning).
    """
    sql_alerts = """
        SELECT ae.stage, ae.matched_rule_codes, ae.created_at
        FROM alert_events ae
        JOIN fb_ads ON fb_ads.id = ae.ad_id
        WHERE fb_ads.fb_ad_id = :fid
          AND ae.created_at >= NOW() - INTERVAL '30 days'
        ORDER BY ae.created_at DESC
        LIMIT 10
    """
    sql_account = """
        SELECT mo.account_id
        FROM meta_api_observation mo
        JOIN fb_ads ON fb_ads.id = mo.ad_id
        WHERE fb_ads.fb_ad_id = :fid
        LIMIT 1
    """
    async with engine.connect() as conn:
        alert_rows = (await conn.execute(text(sql_alerts), {"fid": fb_ad_id})).all()
        acc_row = (await conn.execute(text(sql_account), {"fid": fb_ad_id})).first()

    recent: list[TmaRecentAlert] = []
    for r in alert_rows:
        codes = r.matched_rule_codes or []
        if isinstance(codes, str):
            try:
                codes = json.loads(codes)
            except (ValueError, TypeError):
                codes = []
        reason = ", ".join(str(c) for c in codes) if codes else None
        recent.append(
            TmaRecentAlert(
                stage=(r.stage or "").upper(),
                created_at=r.created_at.isoformat() if r.created_at else None,
                reason_title=reason,
            )
        )
    account_id = acc_row.account_id if acc_row else None
    return recent, account_id


@router.get("/ads/{fb_ad_id}", response_model=TmaAdDetailResponse)
async def tma_ad_detail(
    fb_ad_id: str,
    principal: DepTmaPrincipal,
    engine: DepEngine,
) -> TmaAdDetailResponse:
    """Детальный снимок объявления (build_ad_snapshot + история алертов + account)."""
    snapshots = await build_ad_snapshot(
        engine, fb_ad_ids=[fb_ad_id], include_inactive=True, limit=1
    )
    if not snapshots:
        raise HTTPException(status_code=404, detail="Объявление не найдено")
    snap = snapshots[0]

    recent, account_id = await _load_ad_extras(engine, fb_ad_id)
    m = snap.get("metrics") or {}
    metrics = TmaAdMetrics(
        spend=m.get("spend"),
        leads=m.get("leads"),
        deposits=m.get("deposits"),
        cpc=m.get("cpc"),
        ctr=m.get("ctr"),
        registrations=m.get("registrations"),
        cost_per_lead=m.get("cost_per_lead"),
    )
    return TmaAdDetailResponse(
        fb_ad_id=snap["fb_ad_id"],
        ad_name=snap.get("ad_name"),
        campaign_name=snap.get("campaign_name"),
        adset_name=snap.get("adset_name"),
        offer_code=snap.get("offer_code"),
        state=(snap.get("alert_state") or "normal").upper(),
        snooze_until=snap.get("snoozed_until"),
        account_id=account_id,
        can_open_in_ads_manager=bool(account_id),
        creative_thumb_url=snap.get("creative_thumb_url"),
        creative_image_url=snap.get("creative_image_url"),
        metrics=metrics,
        recent_alerts=recent,
    )


async def _resolve_ad_token(engine: AsyncEngine, fb_ad_id: str) -> tuple[bool, str | None]:
    """Проверяет существование ad + читает текущий open_state_token.

    Возвращает (ad_exists, token_str|None). token нужен для idempotency_key.
    """
    async with engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT a.id, s.open_state_token
                    FROM fb_ads a
                    LEFT JOIN ad_alert_state s ON s.ad_id = a.id
                    WHERE a.fb_ad_id = :fid
                    LIMIT 1
                    """
                ),
                {"fid": fb_ad_id},
            )
        ).first()
    if row is None:
        return False, None
    token = str(row.open_state_token) if row.open_state_token is not None else None
    return True, token


async def _create_disable_action(
    engine: AsyncEngine,
    *,
    fb_ad_id: str,
    token: str | None,
    requested_by: str,
    reason: str | None,
) -> tuple[int | None, str]:
    """Зеркало core.telegram.handlers.alerts.handle_dis_callback.

    Канал — только Marketing API (pause_ad, точно по ad_id). DOM-disable удалён.
    idempotency_key: при активном инциденте дедупим по token (двойной тап безопасен),
    без инцидента — uuid4 (ручное отключение можно повторять).
    """
    suffix = token or uuid.uuid4().hex
    # Мульти-кабинет: кабинет объявления из каталога (None → legacy primary-вкладка).
    from core.observer.accounts import load_ad_account_id_for_fb_ad

    ad_account_id = await load_ad_account_id_for_fb_ad(engine, fb_ad_id)
    payload = MetaMutationPayload(
        mutation_kind="pause_ad",
        target_id=fb_ad_id,
        params={},
        ad_account_id=ad_account_id,
    )
    idempotency_key = f"tma:pause_ad:{fb_ad_id}:{suffix}"
    task_id = await create_mutation_task(
        engine,
        payload=payload,
        requested_by=requested_by,
        status="pending",
        idempotency_key=idempotency_key,
    )
    if task_id is None:
        # Дубль по idempotency_key (двойной тап с тем же token) — ON CONFLICT DO NOTHING
        # вернул None. Достаём id уже существующей задачи, чтобы ответ был идемпотентным.
        async with engine.connect() as conn:
            row = (
                await conn.execute(
                    text("SELECT id FROM task_queue WHERE idempotency_key = :k"),
                    {"k": idempotency_key},
                )
            ).first()
        task_id = int(row[0]) if row else None
    return task_id, "meta_api"


@router.post("/ads/{fb_ad_id}/disable", response_model=TmaDisableResponse)
async def tma_disable_ad(
    fb_ad_id: str,
    body: TmaDisableRequest,
    principal: DepTmaPrincipal,
    engine: DepEngine,
) -> TmaDisableResponse:
    """Создаёт задачу на отключение объявления (money-действие).

    Канал — только Marketing API (meta_api pause_ad, точно по ad_id),
    как ручная кнопка бота. requested_by = tma:<telegram_user_id>.
    """
    exists, db_token = await _resolve_ad_token(engine, fb_ad_id)
    if not exists:
        raise HTTPException(status_code=404, detail="Объявление не найдено")

    requested_by = f"tma:{principal.telegram_user_id}"
    # Idempotency: клиентский body.token приоритетнее open_state_token объявления —
    # повторный тап с тем же token не плодит вторую disable-задачу.
    idem_token = body.token or db_token
    try:
        task_id, channel = await _create_disable_action(
            engine,
            fb_ad_id=fb_ad_id,
            token=idem_token,
            requested_by=requested_by,
            reason=body.reason,
        )
    except Exception as exc:
        logger.exception("tma disable: не удалось создать задачу для %s", fb_ad_id)
        raise HTTPException(status_code=502, detail="Не удалось создать задачу") from exc

    detail = "Задача на отключение принята" if task_id else "Уже в очереди"
    logger.info(
        "TMA disable: ad=%s by=%s channel=%s task_id=%s", fb_ad_id, requested_by, channel, task_id
    )
    return TmaDisableResponse(ok=True, task_id=task_id, channel=channel, detail=detail)


@router.post("/ads/{fb_ad_id}/snooze", response_model=TmaSnoozeResponse)
async def tma_snooze_ad(
    fb_ad_id: str,
    body: TmaSnoozeRequest,
    principal: DepTmaPrincipal,
    engine: DepEngine,
) -> TmaSnoozeResponse:
    """Снуз: ad_alert_state.snoozed_until = now + minutes.

    404 — объявления нет. 409 — у ad нет строки состояния (нечего снузить).
    """
    until = datetime.now(timezone.utc) + timedelta(minutes=body.minutes)
    async with engine.begin() as conn:
        ad_row = (
            await conn.execute(
                text("SELECT id FROM fb_ads WHERE fb_ad_id = :fid LIMIT 1"),
                {"fid": fb_ad_id},
            )
        ).first()
        if ad_row is None:
            raise HTTPException(status_code=404, detail="Объявление не найдено")
        result = await conn.execute(
            text(
                """
                UPDATE ad_alert_state
                SET snoozed_until = :until, updated_at = NOW()
                WHERE ad_id = :ad_id
                """
            ),
            {"until": until, "ad_id": ad_row.id},
        )
    if (result.rowcount or 0) == 0:
        raise HTTPException(
            status_code=409, detail="У объявления нет состояния алерта — нечего снузить"
        )
    logger.info("TMA snooze: ad=%s by=tma:%s до %s", fb_ad_id, principal.telegram_user_id, until)
    return TmaSnoozeResponse(ok=True, snoozed_until=until.isoformat())


@router.post("/ads/{fb_ad_id}/claim", response_model=TmaClaimResponse)
async def tma_claim_ad(
    fb_ad_id: str,
    principal: DepTmaPrincipal,
    engine: DepEngine,
) -> TmaClaimResponse:
    """Claim: взять активный алерт под контроль вручную → alert_state='claimed'.

    Переход warning_sent/stop_sent → claimed (observer перестаёт ре-алертить,
    защита WHERE NOT IN ('claimed','disabled') в apply_fsm_transition). Идемпотентно:
    повторный claim уже-claimed → ok. 404 — ad нет; 409 — нет активного алерта.
    """
    async with engine.begin() as conn:
        ad_row = (
            await conn.execute(
                text(
                    """
                    SELECT a.id, s.alert_state
                    FROM fb_ads a
                    LEFT JOIN ad_alert_state s ON s.ad_id = a.id
                    WHERE a.fb_ad_id = :fid
                    LIMIT 1
                    """
                ),
                {"fid": fb_ad_id},
            )
        ).first()
        if ad_row is None:
            raise HTTPException(status_code=404, detail="Объявление не найдено")

        current = ad_row.alert_state
        if current == "claimed":
            return TmaClaimResponse(ok=True, alert_state="claimed")
        if current not in ("warning_sent", "stop_sent"):
            raise HTTPException(
                status_code=409,
                detail=f"Нет активного алерта для снятия (состояние: {current or 'normal'})",
            )

        await conn.execute(
            text(
                """
                UPDATE ad_alert_state
                SET alert_state = 'claimed', last_transition_at = NOW(), updated_at = NOW()
                WHERE ad_id = :ad_id AND alert_state IN ('warning_sent', 'stop_sent')
                """
            ),
            {"ad_id": ad_row.id},
        )
    logger.info("TMA claim: ad=%s by=tma:%s → claimed", fb_ad_id, principal.telegram_user_id)
    return TmaClaimResponse(ok=True, alert_state="claimed")


# ===========================================================================
# Автостарт кабинета по расписанию (read всем, write — owner-only)
# ===========================================================================


@router.get("/cabinet-autostart", response_model=CabinetAutostartResponse)
async def tma_get_cabinet_autostart(
    principal: DepTmaPrincipal,
    engine: DepEngine,
) -> CabinetAutostartResponse:
    """Текущий конфиг автостарта кабинета (любой авторизованный recipient)."""
    cfg = await read_autostart_config(engine)
    return CabinetAutostartResponse.from_config(cfg)


@router.put("/cabinet-autostart", response_model=CabinetAutostartResponse)
async def tma_put_cabinet_autostart(
    body: CabinetAutostartPutRequest,
    principal: DepTmaPrincipal,
    engine: DepEngine,
) -> CabinetAutostartResponse:
    """Изменить конфиг автостарта. Money-критично → только role='owner'."""
    if not principal.is_owner:
        raise HTTPException(
            status_code=403, detail="Только владелец кабинета может менять автостарт"
        )
    await write_autostart_config(
        engine,
        {
            "enabled": body.enabled,
            "hour_utc": body.hour_utc,
            "minute_utc": body.minute_utc,
        },
    )
    logger.info(
        "cabinet_autostart обновлён (tma:%s): enabled=%s %02d:%02d UTC",
        principal.telegram_user_id,
        body.enabled,
        body.hour_utc,
        body.minute_utc,
    )
    cfg = await read_autostart_config(engine)
    return CabinetAutostartResponse.from_config(cfg)
