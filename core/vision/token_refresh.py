"""Self-heal the PostgreSQL-backed Vision cloud token before it expires."""

from __future__ import annotations

import base64
import json
import logging
import math
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Literal
from urllib.parse import quote

import httpx
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession

from core.crypto import decrypt, encrypt
from core.models.settings.vision_config import VisionConfig
from core.telegram.worker_notify import notify_recurring_incident, resolve_recurring_incident

logger = logging.getLogger(__name__)

VISION_TOKEN_REFRESH_INCIDENT_KEY = "vision:token_refresh"
DEFAULT_REFRESH_BEFORE_DAYS = 5.0
DEFAULT_MIN_ATTEMPT_INTERVAL = timedelta(days=1)
DEFAULT_HTTP_TIMEOUT_SECONDS = 30.0

RefreshOutcome = Literal[
    "refreshed",
    "not_due",
    "throttled",
    "missing_configuration",
    "failed",
    "superseded",
]


class VisionCloudAuthError(RuntimeError):
    """A sanitized Vision cloud authentication failure without response data."""

    def __init__(self, *, stage: str, status_code: int | None = None) -> None:
        self.stage = stage
        self.status_code = status_code
        suffix = f" (HTTP {status_code})" if status_code is not None else ""
        super().__init__(f"Vision cloud authentication failed at {stage}{suffix}")


class VisionRefreshConfigurationError(RuntimeError):
    """Stored refresh material is missing or cannot be decrypted."""


@dataclass(frozen=True, slots=True)
class VisionCloudCredentials:
    username: str = field(repr=False)
    password: str = field(repr=False)
    team_id: str | None = field(default=None, repr=False)
    folder_id: str | None = field(default=None, repr=False)


@dataclass(frozen=True, slots=True)
class VisionTokenRefreshResult:
    outcome: RefreshOutcome
    token_expiration: datetime | None = None


@dataclass(frozen=True, slots=True)
class _RefreshSnapshot:
    config_id: uuid.UUID
    revision: datetime
    token_encrypted: str = field(repr=False)
    username_encrypted: str | None = field(default=None, repr=False)
    password_encrypted: str | None = field(default=None, repr=False)
    team_id_encrypted: str | None = field(default=None, repr=False)
    folder_id_encrypted: str | None = field(default=None, repr=False)
    attempted_at: datetime | None = None


def token_expiration(token: str) -> datetime | None:
    """Return a JWT ``exp`` timestamp without verifying or logging the token."""
    try:
        parts = token.split(".")
        if len(parts) < 2 or not parts[1] or len(parts[1]) > 64_000:
            return None
        payload_segment = parts[1] + "=" * (-len(parts[1]) % 4)
        payload = json.loads(base64.urlsafe_b64decode(payload_segment))
        exp = payload.get("exp")
        if isinstance(exp, bool) or not isinstance(exp, (int, float)):
            return None
        if not math.isfinite(float(exp)):
            return None
        return datetime.fromtimestamp(float(exp), tz=UTC)
    except (KeyError, TypeError, ValueError, OverflowError, json.JSONDecodeError):
        return None


def _extract_token(response: httpx.Response, *, stage: str) -> str:
    if not response.is_success:
        raise VisionCloudAuthError(stage=stage, status_code=response.status_code)
    try:
        payload = response.json()
    except ValueError:
        raise VisionCloudAuthError(stage=stage, status_code=response.status_code) from None
    data = payload.get("data") if isinstance(payload, dict) else None
    token = data.get("token") if isinstance(data, dict) else None
    if not isinstance(token, str) or not token.strip():
        raise VisionCloudAuthError(stage=stage, status_code=response.status_code)
    return token.strip()


async def login_to_vision_cloud(
    vision_cloud_url: str,
    *,
    username: str,
    password: str,
    team_id: str | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> str:
    """Login with user credentials and optionally exchange for a team token."""
    base_url = vision_cloud_url.rstrip("/")
    client = http_client or httpx.AsyncClient(timeout=DEFAULT_HTTP_TIMEOUT_SECONDS)
    owns_client = http_client is None
    try:
        try:
            response = await client.post(
                f"{base_url}/users/auth",
                json={"username": username, "password": password},
            )
        except httpx.HTTPError:
            raise VisionCloudAuthError(stage="user_login") from None
        token = _extract_token(response, stage="user_login")

        normalized_team_id = (team_id or "").strip()
        if not normalized_team_id:
            return token
        team_path = quote(normalized_team_id, safe="")
        try:
            response = await client.get(
                f"{base_url}/teams/{team_path}/auth",
                headers={"X-Token": token},
            )
        except httpx.HTTPError:
            raise VisionCloudAuthError(stage="team_login") from None
        return _extract_token(response, stage="team_login")
    finally:
        if owns_client:
            await client.aclose()


async def _load_refresh_snapshot(engine: AsyncEngine) -> _RefreshSnapshot | None:
    async with AsyncSession(engine) as session:
        row = (
            await session.execute(
                select(
                    VisionConfig.id,
                    VisionConfig.updated_at,
                    VisionConfig.x_token_encrypted,
                    VisionConfig.username_encrypted,
                    VisionConfig.password_encrypted,
                    VisionConfig.team_id_encrypted,
                    VisionConfig.folder_id_encrypted,
                    VisionConfig.token_refresh_attempted_at,
                ).where(VisionConfig.singleton_key == "default")
            )
        ).one_or_none()
    if row is None:
        return None
    return _RefreshSnapshot(
        config_id=row.id,
        revision=row.updated_at,
        token_encrypted=row.x_token_encrypted or "",
        username_encrypted=row.username_encrypted,
        password_encrypted=row.password_encrypted,
        team_id_encrypted=row.team_id_encrypted,
        folder_id_encrypted=row.folder_id_encrypted,
        attempted_at=row.token_refresh_attempted_at,
    )


def _decrypt_optional(value: str | None, *, field_name: str) -> str | None:
    encrypted = (value or "").strip()
    if not encrypted:
        return None
    try:
        plaintext = decrypt(encrypted).strip()
    except Exception:
        raise VisionRefreshConfigurationError(
            f"Vision refresh field {field_name} cannot be decrypted"
        ) from None
    if not plaintext:
        raise VisionRefreshConfigurationError(
            f"Vision refresh field {field_name} cannot be decrypted"
        )
    return plaintext


def _load_credentials(snapshot: _RefreshSnapshot) -> VisionCloudCredentials:
    username = _decrypt_optional(snapshot.username_encrypted, field_name="username")
    password = _decrypt_optional(snapshot.password_encrypted, field_name="password")
    team_id = _decrypt_optional(snapshot.team_id_encrypted, field_name="team_id")
    folder_id = _decrypt_optional(snapshot.folder_id_encrypted, field_name="folder_id")
    if not username or not password:
        raise VisionRefreshConfigurationError("Vision cloud username/password are not configured")
    return VisionCloudCredentials(
        username=username,
        password=password,
        team_id=team_id,
        folder_id=folder_id,
    )


async def _mark_refresh_attempt(
    engine: AsyncEngine,
    *,
    snapshot: _RefreshSnapshot,
    attempted_at: datetime,
    minimum_attempt_interval: timedelta,
) -> bool:
    """Persist the throttle marker without changing the readiness revision."""
    async with engine.begin() as conn:
        marked = await conn.scalar(
            text(
                """
                UPDATE vision_config
                SET token_refresh_attempted_at = :attempted_at
                WHERE id = :config_id
                  AND updated_at = :revision
                  AND (
                    token_refresh_attempted_at IS NULL
                    OR token_refresh_attempted_at <= :retry_before
                  )
                RETURNING id
                """
            ),
            {
                "attempted_at": attempted_at,
                "config_id": snapshot.config_id,
                "revision": snapshot.revision,
                "retry_before": attempted_at - minimum_attempt_interval,
            },
        )
    return marked is not None


async def _store_refreshed_token(
    engine: AsyncEngine,
    *,
    snapshot: _RefreshSnapshot,
    attempted_at: datetime,
    token: str,
) -> bool:
    """Replace only the configuration revision that initiated this login."""
    encrypted_token = encrypt(token)
    async with engine.begin() as conn:
        stored = await conn.scalar(
            text(
                """
                UPDATE vision_config
                SET x_token_encrypted = :token,
                    updated_at = GREATEST(
                        clock_timestamp(),
                        updated_at + INTERVAL '1 microsecond'
                    )
                WHERE id = :config_id
                  AND updated_at = :revision
                  AND token_refresh_attempted_at = :attempted_at
                RETURNING id
                """
            ),
            {
                "token": encrypted_token,
                "config_id": snapshot.config_id,
                "revision": snapshot.revision,
                "attempted_at": attempted_at,
            },
        )
    return stored is not None


async def _notify_refresh_problem(
    engine: AsyncEngine,
    *,
    severity: Literal["warning", "critical"],
    title: str,
    summary: str,
) -> bool:
    return await notify_recurring_incident(
        engine,
        incident_key=VISION_TOKEN_REFRESH_INCIDENT_KEY,
        audience="all",
        event_type="vision_token_refresh_failed",
        severity=severity,
        title=title,
        summary=summary,
        lines=(
            "Проверь доступность облака Vision и сохранённые cloud-креды",
            "Если скан уже недоступен, останови рискованные объявления вручную",
        ),
        risk="Скан потеряет доступ к Vision; авто-стоп не сработает",
        resource_type="vision",
        resource_id="token_refresh",
    )


async def _notify_missing_configuration(engine: AsyncEngine, *, summary: str) -> None:
    logger.critical("Vision token refresh CRITICAL: cloud credentials unavailable")
    await _notify_refresh_problem(
        engine,
        severity="critical",
        title="Автопродление токена Vision не настроено",
        summary=summary,
    )


async def refresh_vision_token_if_needed(
    engine: AsyncEngine,
    *,
    vision_cloud_url: str,
    refresh_before_days: float = DEFAULT_REFRESH_BEFORE_DAYS,
    minimum_attempt_interval: timedelta = DEFAULT_MIN_ATTEMPT_INTERVAL,
    now: datetime | None = None,
    http_client: httpx.AsyncClient | None = None,
) -> VisionTokenRefreshResult:
    """Refresh the canonical token when due and publish one durable incident."""
    current_time = now or datetime.now(UTC)
    if current_time.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    if not math.isfinite(refresh_before_days) or refresh_before_days < 0:
        raise ValueError("refresh_before_days must be a finite non-negative number")
    if minimum_attempt_interval <= timedelta(0):
        raise ValueError("minimum_attempt_interval must be positive")

    snapshot = await _load_refresh_snapshot(engine)
    if snapshot is None:
        await _notify_missing_configuration(
            engine,
            summary="В PostgreSQL нет строки vision_config: скан не получит рабочий токен.",
        )
        return VisionTokenRefreshResult("missing_configuration")

    try:
        credentials = _load_credentials(snapshot)
    except VisionRefreshConfigurationError:
        await _notify_missing_configuration(
            engine,
            summary=(
                "В vision_config нет рабочего логина/пароля Vision. "
                "Без них приложение не сможет продлить токен."
            ),
        )
        return VisionTokenRefreshResult("missing_configuration")

    try:
        current_token = _decrypt_optional(snapshot.token_encrypted, field_name="x_token") or ""
    except VisionRefreshConfigurationError:
        current_token = ""
    expires_at = token_expiration(current_token) if current_token else None
    token_unavailable = not current_token
    token_expired = expires_at is not None and expires_at <= current_time
    refresh_due = (
        token_unavailable
        or expires_at is None
        or expires_at - current_time < timedelta(days=refresh_before_days)
    )

    if not refresh_due:
        await resolve_recurring_incident(
            engine,
            incident_key=VISION_TOKEN_REFRESH_INCIDENT_KEY,
            audience="all",
            summary="Токен Vision действителен, автопродление готово.",
        )
        return VisionTokenRefreshResult("not_due", expires_at)

    is_critical = token_unavailable or token_expired
    if is_critical:
        logger.critical("Vision token refresh CRITICAL: canonical token is unavailable or expired")
        await _notify_refresh_problem(
            engine,
            severity="critical",
            title="Токен Vision истёк или недоступен",
            summary="Скан уже не может надёжно работать; приложение пытается получить новый токен.",
        )

    attempted_at = snapshot.attempted_at
    if attempted_at is not None and attempted_at > current_time - minimum_attempt_interval:
        return VisionTokenRefreshResult("throttled", expires_at)

    marked = await _mark_refresh_attempt(
        engine,
        snapshot=snapshot,
        attempted_at=current_time,
        minimum_attempt_interval=minimum_attempt_interval,
    )
    if not marked:
        return VisionTokenRefreshResult("throttled", expires_at)

    try:
        new_token = await login_to_vision_cloud(
            vision_cloud_url,
            username=credentials.username,
            password=credentials.password,
            team_id=credentials.team_id,
            http_client=http_client,
        )
    except Exception as exc:  # noqa: BLE001 - every refresh failure must become observable
        stage = exc.stage if isinstance(exc, VisionCloudAuthError) else "unexpected"
        status_code = exc.status_code if isinstance(exc, VisionCloudAuthError) else None
        severity: Literal["warning", "critical"] = "critical" if is_critical else "warning"
        log_method = logger.critical if severity == "critical" else logger.warning
        log_method(
            "Vision token refresh %s: cloud login failed (stage=%s, error_type=%s, http_status=%s)",
            severity.upper(),
            stage,
            type(exc).__name__,
            status_code,
        )
        await _notify_refresh_problem(
            engine,
            severity=severity,
            title=(
                "Токен Vision не восстановлен"
                if severity == "critical"
                else "Не удалось обновить токен Vision"
            ),
            summary=(
                "Токен уже недоступен: скан слепнет, а авто-стоп не сработает."
                if severity == "critical"
                else "Текущий токен пока действует, но автоматическое продление не сработало."
            ),
        )
        return VisionTokenRefreshResult("failed", expires_at)

    stored = await _store_refreshed_token(
        engine,
        snapshot=snapshot,
        attempted_at=current_time,
        token=new_token,
    )
    if not stored:
        logger.info("Vision token refresh superseded by a newer configuration revision")
        return VisionTokenRefreshResult("superseded", expires_at)

    logger.info("Vision token refreshed in PostgreSQL; browser readiness invalidated")
    await resolve_recurring_incident(
        engine,
        incident_key=VISION_TOKEN_REFRESH_INCIDENT_KEY,
        audience="all",
        summary="Токен Vision обновлён, канал готов к повторной проверке.",
    )
    return VisionTokenRefreshResult("refreshed", token_expiration(new_token))


__all__ = [
    "DEFAULT_MIN_ATTEMPT_INTERVAL",
    "DEFAULT_REFRESH_BEFORE_DAYS",
    "VISION_TOKEN_REFRESH_INCIDENT_KEY",
    "VisionCloudAuthError",
    "VisionTokenRefreshResult",
    "login_to_vision_cloud",
    "refresh_vision_token_if_needed",
    "token_expiration",
]
