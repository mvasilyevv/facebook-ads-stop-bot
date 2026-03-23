from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from core.config import Settings
from core.repositories.operations import SystemSettingsRepository

SERVICE_SETTING_AUTO_PAUSE_ENABLED = "auto_pause_enabled"
SERVICE_SETTING_AUTO_RESUME_ENABLED = "auto_resume_enabled"
SERVICE_SETTING_OBSERVE_ONLY_ENABLED = "observe_only_enabled"
SERVICE_SETTING_SCAN_INTERVAL_SECONDS = "scan_interval_seconds"
SERVICE_SETTING_VISION_API_TOKEN = "vision_api_token"
SERVICE_SETTING_VISION_LOCAL_API_URL = "vision_local_api_url"
SERVICE_SETTING_VISION_CLOUD_API_URL = "vision_cloud_api_url"
SERVICE_SETTING_TELEGRAM_BOT_TOKEN = "telegram_bot_token"
SERVICE_SETTING_TELEGRAM_CHAT_ID = "telegram_chat_id"

SERVICE_SETTING_KEYS = (
    SERVICE_SETTING_AUTO_PAUSE_ENABLED,
    SERVICE_SETTING_AUTO_RESUME_ENABLED,
    SERVICE_SETTING_OBSERVE_ONLY_ENABLED,
    SERVICE_SETTING_SCAN_INTERVAL_SECONDS,
    SERVICE_SETTING_VISION_API_TOKEN,
    SERVICE_SETTING_VISION_LOCAL_API_URL,
    SERVICE_SETTING_VISION_CLOUD_API_URL,
    SERVICE_SETTING_TELEGRAM_BOT_TOKEN,
    SERVICE_SETTING_TELEGRAM_CHAT_ID,
)
_ALLOWED_SCAN_INTERVALS = (30, 60, 120, 300)


@dataclass(slots=True, frozen=True)
class ServiceRuntimeSettings:
    """Эффективные runtime-настройки сервиса с учётом БД и env."""

    auto_pause_enabled: bool
    auto_resume_enabled: bool
    auto_resume_available: bool
    observe_only_enabled: bool
    scan_interval_seconds: int
    vision_api_token: str
    vision_local_api_url: str
    vision_cloud_api_url: str
    telegram_bot_token: str
    telegram_chat_id: str
    updated_at: datetime


def _parse_bool(value: str | None, fallback: bool) -> bool:
    if value is None:
        return fallback
    return value.strip().lower() in {"true", "1", "yes", "on"}


def _parse_scan_interval(value: str | None, fallback: int) -> int:
    candidate = fallback
    if value is not None:
        try:
            candidate = int(value.strip())
        except ValueError:
            candidate = fallback
    return candidate if candidate in _ALLOWED_SCAN_INTERVALS else fallback


def _coerce_text(value: str | None, fallback: str) -> str:
    if value is None:
        return fallback
    return value.strip()


def mask_secret(value: str) -> str | None:
    """Маскирует секрет для безопасного показа в UI."""

    if not value:
        return None
    if len(value) <= 4:
        return "•" * len(value)
    return f"{'•' * max(len(value) - 4, 4)}{value[-4:]}"


async def resolve_service_settings(
    session,
    *,
    base_settings: Settings | None = None,
) -> ServiceRuntimeSettings:
    """Возвращает runtime-настройки, считанные из БД с fallback на env."""

    base_settings = base_settings or Settings()
    repo = SystemSettingsRepository(session)
    stored = await repo.get_all_settings()

    relevant_updates: list[datetime] = []
    for key in SERVICE_SETTING_KEYS:
        setting = await repo.get_setting(key)
        if setting is not None:
            relevant_updates.append(setting.updated_at)

    fallback_scan_interval = (
        base_settings.worker_scan_interval_seconds
        if base_settings.worker_scan_interval_seconds in _ALLOWED_SCAN_INTERVALS
        else 120
    )
    auto_resume_available = bool(base_settings.feature_auto_resume)
    auto_resume_enabled = _parse_bool(
        stored.get(SERVICE_SETTING_AUTO_RESUME_ENABLED),
        base_settings.feature_auto_resume,
    )
    if not auto_resume_available:
        auto_resume_enabled = False

    return ServiceRuntimeSettings(
        auto_pause_enabled=_parse_bool(
            stored.get(SERVICE_SETTING_AUTO_PAUSE_ENABLED),
            base_settings.feature_auto_pause,
        ),
        auto_resume_enabled=auto_resume_enabled,
        auto_resume_available=auto_resume_available,
        observe_only_enabled=_parse_bool(
            stored.get(SERVICE_SETTING_OBSERVE_ONLY_ENABLED),
            base_settings.feature_observe_only,
        ),
        scan_interval_seconds=_parse_scan_interval(
            stored.get(SERVICE_SETTING_SCAN_INTERVAL_SECONDS),
            fallback_scan_interval,
        ),
        vision_api_token=_coerce_text(
            stored.get(SERVICE_SETTING_VISION_API_TOKEN),
            base_settings.vision_api_token,
        ),
        vision_local_api_url=_coerce_text(
            stored.get(SERVICE_SETTING_VISION_LOCAL_API_URL),
            base_settings.vision_local_api_url,
        ),
        vision_cloud_api_url=_coerce_text(
            stored.get(SERVICE_SETTING_VISION_CLOUD_API_URL),
            base_settings.vision_cloud_api_url,
        ),
        telegram_bot_token=_coerce_text(
            stored.get(SERVICE_SETTING_TELEGRAM_BOT_TOKEN),
            base_settings.telegram_bot_token,
        ),
        telegram_chat_id=_coerce_text(
            stored.get(SERVICE_SETTING_TELEGRAM_CHAT_ID),
            base_settings.telegram_chat_id,
        ),
        updated_at=max(relevant_updates) if relevant_updates else datetime.now(tz=UTC),
    )


def build_effective_settings(
    base_settings: Settings,
    runtime_settings: ServiceRuntimeSettings,
) -> Settings:
    """Возвращает копию env-настроек, обогащённую значениями из БД."""

    return base_settings.model_copy(
        update={
            "vision_api_token": runtime_settings.vision_api_token,
            "vision_local_api_url": runtime_settings.vision_local_api_url,
            "vision_cloud_api_url": runtime_settings.vision_cloud_api_url,
            "telegram_bot_token": runtime_settings.telegram_bot_token,
            "telegram_chat_id": runtime_settings.telegram_chat_id,
            "worker_scan_interval_seconds": runtime_settings.scan_interval_seconds,
            "feature_auto_pause": runtime_settings.auto_pause_enabled,
            "feature_auto_resume": runtime_settings.auto_resume_enabled,
            "feature_observe_only": runtime_settings.observe_only_enabled,
        }
    )
