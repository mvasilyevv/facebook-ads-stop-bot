# -*- coding: utf-8 -*-
"""FastAPI роутер для настроек Vision (settings_vision).

Endpoints под /api (благодаря auto-discovery с prefix="/api"):
- GET  /settings/vision   — VisionConfig + direct browser-agent gRPC probe
- PUT  /settings/vision   — обновить token/profile и cloud-креды
- POST /vision/reconnect  — gRPC ReconnectBrowser к browser-agent
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import datetime

from fastapi import APIRouter, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from apps.api.deps import DepEngine, DepMetaApiClient, DepSettings
from apps.api.routers.v1.schemas.settings_vision import (
    VisionEnsureCdpResponse,
    VisionReconnectResponse,
    VisionSettingsResponse,
    VisionSettingsUpdateRequest,
)
from clients.python_grpc.client import BrowserAgentClient, BrowserAgentConfig
from core.meta_api.client import BROWSER_CONTRACT_VERSION
from core.models.settings.vision_config import VisionConfig
from core.tasks.browser_fence import (
    BrowserExclusiveMaintenance,
    BrowserFenceLeaseLost,
    BrowserMaintenanceGuard,
    BrowserMaintenanceOwnerInvalid,
    BrowserOperationBlocked,
    BrowserOperationDrainTimeout,
    BrowserOperationFence,
)
from core.vision.channel import (
    VisionChannelAssessment,
    assess_vision_channel,
)
from core.vision.cloud_probe import probe_vision_cloud
from core.vision_runtime import VisionConfigurationError, load_vision_runtime_config

logger = logging.getLogger(__name__)

# Роутер для /settings/vision
_settings_router = APIRouter(prefix="/settings/vision", tags=["settings"])

# Роутер для /vision (reconnect)
_vision_router = APIRouter(prefix="/vision", tags=["settings"])


# ---------------------------------------------------------------------------
# Snapshot — безопасная передача данных вне session
# ---------------------------------------------------------------------------


@dataclass
class _VisionSnapshot:
    """Скалярные поля VisionConfig без ORM-ленивой загрузки."""

    x_token_encrypted: str | None = field(repr=False)
    profile_id: str | None
    updated_at: datetime
    username_encrypted: str | None = field(default=None, repr=False)
    password_encrypted: str | None = field(default=None, repr=False)
    team_id_encrypted: str | None = field(default=None, repr=False)
    folder_id_encrypted: str | None = field(default=None, repr=False)


@dataclass(frozen=True)
class _BrowserChannelProbe:
    """Fail-closed browser readiness bound to one canonical Vision profile."""

    status: str
    message: str | None
    browser_contract_version: int | None
    browser_contract_compatible: bool
    browser_session_id: str | None = None
    live_profile_id: str | None = None
    graph_probe_performed: bool = False
    graph_probe_ok: bool = False
    maintenance_recovery_allowed: bool = False


def _is_restartable_probe_failure(detail: str) -> bool:
    normalized = detail.strip().casefold()
    return any(
        marker in normalized
        for marker in (
            "probe_network_down",
            "failed to fetch",
            "networkerror",
            "network down",
            "network unavailable",
        )
    )


def _snapshot(config: VisionConfig | None) -> _VisionSnapshot | None:
    """Считывает нужные поля из ORM-объекта ВНУТРИ session и возвращает скаляры."""
    if config is None:
        return None
    return _VisionSnapshot(
        x_token_encrypted=config.x_token_encrypted,
        username_encrypted=config.username_encrypted,
        password_encrypted=config.password_encrypted,
        team_id_encrypted=config.team_id_encrypted,
        folder_id_encrypted=config.folder_id_encrypted,
        profile_id=config.profile_id,
        updated_at=config.updated_at,
    )


def _refresh_state(snapshot: _VisionSnapshot | None) -> dict[str, bool]:
    """Expose operator state without ever returning encrypted or plaintext secrets."""
    has_username = bool(snapshot and (snapshot.username_encrypted or "").strip())
    has_password = bool(snapshot and (snapshot.password_encrypted or "").strip())
    return {
        "has_cloud_credentials": has_username and has_password,
        "has_cloud_username": has_username,
        "has_cloud_password": has_password,
        "has_team_id": bool(snapshot and (snapshot.team_id_encrypted or "").strip()),
        "has_folder_id": bool(snapshot and (snapshot.folder_id_encrypted or "").strip()),
    }


async def _load_config(session: AsyncSession) -> VisionConfig | None:
    """Читает singleton VisionConfig или возвращает None, если строки нет."""
    return await session.scalar(select(VisionConfig).where(VisionConfig.singleton_key == "default"))


async def _diagnose_vision_channel(
    engine: AsyncEngine,
    meta_api_client: object | None,
    settings: object,
    snapshot: _VisionSnapshot | None,
    maintenance_owner: str = "",
) -> tuple[_BrowserChannelProbe, VisionChannelAssessment]:
    """Проверить cloud/profile и затем browser-agent, не раскрывая secrets."""

    has_token = bool(snapshot and (snapshot.x_token_encrypted or "").strip())
    profile_configured = bool(snapshot and (snapshot.profile_id or "").strip())
    refresh_state = _refresh_state(snapshot)
    runtime = None
    if has_token and profile_configured:
        try:
            runtime = await load_vision_runtime_config(engine)
        except VisionConfigurationError:
            runtime = None

    cloud_state = None
    if runtime is not None:
        try:
            cloud_state = (
                await probe_vision_cloud(
                    settings.vision_cloud_url,  # type: ignore[attr-defined]
                    token=runtime.x_token,
                    profile_id=runtime.profile_id,
                )
            ).state
        except Exception as exc:  # noqa: BLE001 - diagnostics must fail closed
            logger.warning("Vision cloud probe failed: error_type=%s", type(exc).__name__)
            cloud_state = "unavailable"

    empty_probe = _BrowserChannelProbe("UNKNOWN", None, None, False)
    browser_probe = empty_probe
    if cloud_state == "ready" and runtime is not None:
        browser_probe = await _fenced_settings_probe(
            engine,
            meta_api_client,
            expected_profile_id=runtime.profile_id,
            maintenance_owner=maintenance_owner,
        )

    assessment = assess_vision_channel(
        has_token=has_token,
        profile_configured=profile_configured,
        has_cloud_credentials=refresh_state["has_cloud_credentials"],
        cloud_state=cloud_state,
        browser_status=browser_probe.status,  # type: ignore[arg-type]
    )
    return browser_probe, assessment


async def _probe_browser_channel(
    meta_api_client: object | None,
    *,
    expected_profile_id: str,
) -> _BrowserChannelProbe:
    """Probe the exact configured profile with a real Graph request.

    A preferred or merely connected browser session is not evidence for the
    configured profile. READY requires contract compatibility, exact live
    identity and a completed successful Graph probe.
    """
    expected_profile_id = expected_profile_id.strip()
    if not expected_profile_id:
        return _BrowserChannelProbe(
            "UNAVAILABLE",
            "canonical Vision profile is not configured",
            None,
            False,
        )
    if meta_api_client is None:
        return _BrowserChannelProbe(
            "UNKNOWN",
            "API process has no browser-agent channel",
            None,
            False,
        )
    try:
        result = await meta_api_client.check_health(  # type: ignore[attr-defined]
            full_probe=True,
            expected_profile_id=expected_profile_id,
        )
    except Exception as exc:  # noqa: BLE001 - unavailable is a valid operator state
        logger.warning("browser-agent channel probe failed: %s", type(exc).__name__)
        return _BrowserChannelProbe(
            "UNAVAILABLE",
            "browser-agent channel unavailable",
            None,
            False,
        )

    observed_contract = int(result.get("browser_contract_version") or 0)
    compatible = observed_contract == BROWSER_CONTRACT_VERSION
    session_id = str(result.get("session_id") or "").strip() or None
    live_profile_id = str(result.get("vision_profile_id") or "").strip() or None
    probe_performed = bool(result.get("probe_performed"))
    probe_ok = bool(result.get("probe_ok"))

    if not compatible:
        return _BrowserChannelProbe(
            "DEGRADED",
            (
                "browser-agent contract is incompatible "
                f"(required={BROWSER_CONTRACT_VERSION}, observed={observed_contract})"
            ),
            observed_contract or None,
            False,
            session_id,
            live_profile_id,
            probe_performed,
            probe_ok,
        )

    maintenance_recovery_allowed = False
    if not session_id or not live_profile_id:
        message = "browser-agent did not prove a concrete live Vision identity"
        maintenance_recovery_allowed = True
    elif live_profile_id != expected_profile_id:
        message = "live Vision profile does not match canonical PostgreSQL configuration"
        maintenance_recovery_allowed = True
    elif not probe_performed:
        message = "browser-agent did not perform the required Graph probe"
    elif not probe_ok:
        message = str(result.get("probe_detail") or result.get("detail") or "Graph probe failed")
        maintenance_recovery_allowed = _is_restartable_probe_failure(message)
    elif not bool(result.get("healthy")):
        message = str(result.get("detail") or "browser session is not ready")
        maintenance_recovery_allowed = _is_restartable_probe_failure(message)
    else:
        return _BrowserChannelProbe(
            "READY",
            None,
            observed_contract,
            True,
            session_id,
            live_profile_id,
            True,
            True,
        )

    return _BrowserChannelProbe(
        "DEGRADED",
        message[:240],
        observed_contract,
        True,
        session_id,
        live_profile_id,
        probe_performed,
        probe_ok,
        maintenance_recovery_allowed,
    )


async def _fenced_settings_probe(
    engine: AsyncEngine,
    meta_api_client: object | None,
    *,
    expected_profile_id: str,
    maintenance_owner: str = "",
) -> _BrowserChannelProbe:
    """Register normal reads, or adopt the exact active maintenance owner."""
    try:
        if maintenance_owner:
            guard = BrowserMaintenanceGuard(engine, maintenance_owner)
            async with guard:
                probe = await _probe_browser_channel(
                    meta_api_client,
                    expected_profile_id=expected_profile_id,
                )
                await guard.assert_held()
                return probe
        async with BrowserOperationFence(
            engine,
            operation_kind="vision_settings_probe",
            target=expected_profile_id[:128],
        ) as fence:
            probe = await _probe_browser_channel(
                meta_api_client,
                expected_profile_id=expected_profile_id,
            )
            await fence.assert_held()
            return probe
    except (BrowserMaintenanceOwnerInvalid, BrowserOperationBlocked):
        return _BrowserChannelProbe(
            "UNAVAILABLE",
            "Vision maintenance is active",
            None,
            False,
        )
    except BrowserFenceLeaseLost:
        return _BrowserChannelProbe(
            "UNAVAILABLE",
            "Vision settings probe lost its maintenance fence",
            None,
            False,
        )


# ---------------------------------------------------------------------------
# /settings/vision
# ---------------------------------------------------------------------------


@_settings_router.get("", response_model=VisionSettingsResponse)
async def get_vision_settings(
    request: Request,
    engine: DepEngine,
    meta_api_client: DepMetaApiClient,
    settings: DepSettings,
) -> VisionSettingsResponse:
    """Возвращает canonical PostgreSQL VisionConfig и browser-agent status."""
    async with AsyncSession(engine) as session:
        config = await _load_config(session)
        snap = _snapshot(config)

    probe, assessment = await _diagnose_vision_channel(
        engine,
        meta_api_client,
        settings,
        snap,
        maintenance_owner=request.headers.get(
            "X-FB-Agent-Browser-Maintenance-Owner",
            "",
        ),
    )

    profile_id: str | None = None
    if snap and snap.profile_id:
        profile_id = snap.profile_id.strip() or None

    return VisionSettingsResponse(
        has_token=bool(snap and (snap.x_token_encrypted or "").strip()),
        **_refresh_state(snap),
        profile_id=profile_id,
        configuration_revision=snap.updated_at.isoformat() if snap else None,
        channel_status=assessment.status,
        channel_reason=assessment.reason,
        channel_message=assessment.message,
        channel_next_step=assessment.next_step,
        required_browser_contract_version=BROWSER_CONTRACT_VERSION,
        browser_contract_version=probe.browser_contract_version,
        browser_contract_compatible=probe.browser_contract_compatible,
        browser_session_id=probe.browser_session_id,
        live_profile_id=probe.live_profile_id,
        graph_probe_performed=probe.graph_probe_performed,
        graph_probe_ok=probe.graph_probe_ok,
    )


@_settings_router.put("", response_model=VisionSettingsResponse)
async def put_vision_settings(
    body: VisionSettingsUpdateRequest,
    engine: DepEngine,
    meta_api_client: DepMetaApiClient,
    settings: DepSettings,
) -> VisionSettingsResponse:
    """Обновляет token/profile и cloud-креды в VisionConfig singleton.

    Если x_token передан — шифрует и сохраняет.
    Если profile_id или cloud-поле передано — обновляет только это поле.
    Пустое cloud-поле удаляет сохранённое значение; отсутствие поля его не меняет.
    Если строки ещё нет — создаёт с server-defaults.
    """
    from core.crypto import encrypt

    async def persist() -> _VisionSnapshot:
        async with AsyncSession(engine) as session:
            config = await _load_config(session)
            if config is None:
                config = VisionConfig(
                    x_token_encrypted="",
                    profile_id="",
                )
                session.add(config)

            if body.x_token is not None:
                config.x_token_encrypted = encrypt(body.x_token) if body.x_token else ""
            if body.profile_id is not None:
                config.profile_id = body.profile_id
            refresh_material_changed = False
            for request_field, model_field in (
                ("username", "username_encrypted"),
                ("password", "password_encrypted"),
                ("team_id", "team_id_encrypted"),
                ("folder_id", "folder_id_encrypted"),
            ):
                secret = getattr(body, request_field)
                if secret is None:
                    continue
                plaintext = secret.get_secret_value()
                setattr(config, model_field, encrypt(plaintext) if plaintext else None)
                refresh_material_changed = True
            if refresh_material_changed:
                # Исправленные креды должны разрешить следующую суточную попытку,
                # а не ждать старого throttle-marker.
                config.token_refresh_attempted_at = None
            await session.flush()
            await session.refresh(config)
            result = _snapshot(config)
            if result is None:  # pragma: no cover - the row was just created/loaded
                raise RuntimeError("Vision configuration snapshot disappeared")
            await session.commit()
            return result

    requires_exclusive_fence = bool(body.model_fields_set)
    try:
        if requires_exclusive_fence:
            async with BrowserExclusiveMaintenance(
                engine,
                operation_kind="vision_config_update",
            ) as fence:
                snap = await persist()
                await fence.assert_held()
        else:
            snap = await persist()
    except BrowserOperationBlocked as exc:
        raise HTTPException(
            status_code=409,
            detail="Vision maintenance is active; configuration was not changed",
        ) from exc
    except BrowserOperationDrainTimeout as exc:
        raise HTTPException(
            status_code=409,
            detail="Active browser work did not drain; configuration was not changed",
        ) from exc
    except BrowserFenceLeaseLost as exc:
        raise HTTPException(
            status_code=503,
            detail="Vision configuration fence was lost; retry after reconciliation",
        ) from exc

    probe, assessment = await _diagnose_vision_channel(
        engine,
        meta_api_client,
        settings,
        snap,
    )

    profile_id_val: str | None = None
    if snap and snap.profile_id:
        profile_id_val = snap.profile_id.strip() or None

    return VisionSettingsResponse(
        has_token=bool(snap and (snap.x_token_encrypted or "").strip()),
        **_refresh_state(snap),
        profile_id=profile_id_val,
        configuration_revision=snap.updated_at.isoformat() if snap else None,
        channel_status=assessment.status,
        channel_reason=assessment.reason,
        channel_message=assessment.message,
        channel_next_step=assessment.next_step,
        required_browser_contract_version=BROWSER_CONTRACT_VERSION,
        browser_contract_version=probe.browser_contract_version,
        browser_contract_compatible=probe.browser_contract_compatible,
        browser_session_id=probe.browser_session_id,
        live_profile_id=probe.live_profile_id,
        graph_probe_performed=probe.graph_probe_performed,
        graph_probe_ok=probe.graph_probe_ok,
    )


# ---------------------------------------------------------------------------
# /vision/reconnect
# ---------------------------------------------------------------------------


async def _browser_agent_client(
    engine: AsyncEngine,
    settings: object,
) -> BrowserAgentClient:
    """Build a browser-agent client from canonical PostgreSQL credentials."""
    runtime = await load_vision_runtime_config(engine)
    api_url = settings.vision_api_url  # type: ignore[attr-defined]
    return BrowserAgentClient(
        BrowserAgentConfig(
            vision_x_token=runtime.x_token,
            vision_api_url=api_url,
            vision_profile_id=runtime.profile_id,
            # Без folder_id остановленный профиль отсутствует в /list, и reconnect
            # не может вызвать Vision /start. Каноническое значение живёт в БД.
            vision_folder_id=runtime.folder_id,
            # grpc_host/port из env — иначе в Docker api пойдёт на localhost:50051
            # (browser-agent на хосте). Зеркало фикса observer (main.py).
            grpc_host=os.environ.get("BROWSER_AGENT_HOST", "localhost"),
            grpc_port=int(os.environ.get("BROWSER_AGENT_GRPC_PORT", "50051")),
        )
    )


async def _reconnect_browser(engine: AsyncEngine, settings: object) -> None:
    """Connect to the canonical browser profile without forcing its lifecycle."""
    client = await _browser_agent_client(engine, settings)
    try:
        await client.start()
        await client.reconnect_browser()
    finally:
        try:
            await client.close()
        except Exception:
            pass


async def _recover_browser_profile_under_maintenance(
    engine: AsyncEngine,
    settings: object,
    *,
    maintenance_owner: str,
) -> None:
    """Force-restart Vision only after the caller proved exclusive ownership."""
    client = await _browser_agent_client(engine, settings)
    try:
        await client.start()
        await client.recover_browser_profile_under_maintenance(
            maintenance_owner=maintenance_owner,
        )
    finally:
        try:
            await client.close()
        except Exception:
            pass


@_vision_router.post("/reconnect", response_model=VisionReconnectResponse)
async def post_vision_reconnect(
    engine: DepEngine,
    settings: DepSettings,
) -> VisionReconnectResponse:
    """Триггерит gRPC ReconnectBrowser к browser-agent.

    Читает x_token и profile_id только из PostgreSQL.
    Возвращает 503 при недоступности gRPC.
    """
    import grpc

    try:
        async with BrowserExclusiveMaintenance(
            engine,
            operation_kind="vision_reconnect",
        ) as fence:
            await _reconnect_browser(engine, settings)
            await fence.assert_held()
    except BrowserOperationBlocked as exc:
        raise HTTPException(
            status_code=409,
            detail="Vision maintenance is active; reconnect was not started",
        ) from exc
    except BrowserOperationDrainTimeout as exc:
        raise HTTPException(
            status_code=409,
            detail="Active browser work did not drain; reconnect was not started",
        ) from exc
    except BrowserFenceLeaseLost as exc:
        raise HTTPException(
            status_code=503,
            detail="Vision reconnect fence was lost; state requires reconciliation",
        ) from exc
    except VisionConfigurationError as exc:
        raise HTTPException(status_code=409, detail="Vision runtime не настроен") from exc
    except grpc.RpcError as exc:
        raise HTTPException(
            status_code=503,
            detail="gRPC browser-agent недоступен",
        ) from exc
    except Exception as exc:
        # LOW (аудит 02.07): голый Exception может нести внутренние детали (пути,
        # креды в traceback) — клиенту генерик-текст, диагностика в лог.
        logger.exception("Ошибка переподключения к browser-agent")
        raise HTTPException(
            status_code=503,
            detail="Ошибка переподключения к browser-agent — подробности в логе сервера",
        ) from exc

    return VisionReconnectResponse(status="reconnected")


@_vision_router.post("/ensure-cdp", response_model=VisionEnsureCdpResponse)
async def post_vision_ensure_cdp(
    request: Request,
    engine: DepEngine,
    settings: DepSettings,
    meta_api_client: DepMetaApiClient,
) -> VisionEnsureCdpResponse:
    """Bootstrap browser channel: direct probe, then exclusive recovery when needed.

    Никогда не падает 5xx — всегда {ok,status,action,message}. Если CDP уже готов,
    action=none. Иначе подтверждённый maintenance owner разрешает ровно один
    принудительный restart canonical Vision-профиля с обязательным повторным probe.
    """
    maintenance_owner = request.headers.get(
        "X-FB-Agent-Browser-Maintenance-Owner",
        "",
    )
    try:
        guard = BrowserMaintenanceGuard(engine, maintenance_owner)
        async with guard:
            try:
                runtime = await load_vision_runtime_config(engine)
            except VisionConfigurationError:
                return VisionEnsureCdpResponse(
                    ok=False,
                    status="UNAVAILABLE",
                    action="none",
                    message="Vision is not configured in PostgreSQL",
                )

            probe = await _probe_browser_channel(
                meta_api_client,
                expected_profile_id=runtime.profile_id,
            )
            if probe.status == "READY":
                await guard.assert_held()
                return VisionEnsureCdpResponse(
                    ok=True,
                    status="READY",
                    action="none",
                    message="Browser-agent channel is ready",
                )
            if not probe.maintenance_recovery_allowed:
                await guard.assert_held()
                return VisionEnsureCdpResponse(
                    ok=False,
                    status=probe.status,
                    action="none",
                    message=probe.message or "Browser channel requires operator action",
                )

            try:
                await guard.assert_held()
                await _recover_browser_profile_under_maintenance(
                    engine,
                    settings,
                    maintenance_owner=maintenance_owner,
                )
                await guard.assert_held()
            except Exception as exc:
                logger.warning("ensure-cdp: profile recovery failed: %s", type(exc).__name__)
                return VisionEnsureCdpResponse(
                    ok=False,
                    status="UNAVAILABLE",
                    action="restart",
                    message="Browser-agent profile recovery failed",
                )

            probe = await _probe_browser_channel(
                meta_api_client,
                expected_profile_id=runtime.profile_id,
            )
            await guard.assert_held()
    except (
        BrowserMaintenanceOwnerInvalid,
        BrowserFenceLeaseLost,
        BrowserOperationBlocked,
    ) as exc:
        logger.warning("ensure-cdp: maintenance owner rejected: %s", type(exc).__name__)
        return VisionEnsureCdpResponse(
            ok=False,
            status="UNAVAILABLE",
            action="none",
            message="Platform maintenance ownership is missing or expired",
        )

    return VisionEnsureCdpResponse(
        ok=probe.status == "READY",
        status="RECOVERED" if probe.status == "READY" else "UNAVAILABLE",
        action="restart",
        message=(
            "Browser-agent profile recovered"
            if probe.status == "READY"
            else "Profile restart completed but the channel is not ready"
        ),
    )


# ---------------------------------------------------------------------------
# Экспорт единого router
# ---------------------------------------------------------------------------

# auto-discovery ищет атрибут `router` в модуле — объединяем sub-router'ы.
router = APIRouter(tags=["settings"])
router.include_router(_settings_router)
router.include_router(_vision_router)
