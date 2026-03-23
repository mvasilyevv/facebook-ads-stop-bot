from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from core.config import Settings
from core.repositories.operations import SystemSettingsRepository

SERVICE_SETTING_AUTO_PAUSE_ENABLED = "auto_pause_enabled"
SERVICE_SETTING_AUTO_RESUME_ENABLED = "auto_resume_enabled"
SERVICE_SETTING_OBSERVE_ONLY_ENABLED = "observe_only_enabled"
SERVICE_SETTING_FULL_SCAN_INTERVAL_SECONDS = "full_scan_interval_seconds"
SERVICE_SETTING_SCAN_INTERVAL_SECONDS = SERVICE_SETTING_FULL_SCAN_INTERVAL_SECONDS
SERVICE_SETTING_RECHECK_INTERVAL_SECONDS = "recheck_interval_seconds"
SERVICE_SETTING_FULL_SCAN_PROFILE_CONCURRENCY = "full_scan_profile_concurrency"
SERVICE_SETTING_ACTION_WORKER_CONCURRENCY = "action_worker_concurrency"
SERVICE_SETTING_VISION_API_TOKEN = "vision_api_token"
SERVICE_SETTING_VISION_LOCAL_API_URL = "vision_local_api_url"
SERVICE_SETTING_VISION_CLOUD_API_URL = "vision_cloud_api_url"
SERVICE_SETTING_TELEGRAM_BOT_TOKEN = "telegram_bot_token"
SERVICE_SETTING_TELEGRAM_CHAT_ID = "telegram_chat_id"

SERVICE_SETTING_KEYS = (
    SERVICE_SETTING_AUTO_PAUSE_ENABLED,
    SERVICE_SETTING_AUTO_RESUME_ENABLED,
    SERVICE_SETTING_OBSERVE_ONLY_ENABLED,
    SERVICE_SETTING_FULL_SCAN_INTERVAL_SECONDS,
    SERVICE_SETTING_RECHECK_INTERVAL_SECONDS,
    SERVICE_SETTING_FULL_SCAN_PROFILE_CONCURRENCY,
    SERVICE_SETTING_ACTION_WORKER_CONCURRENCY,
    SERVICE_SETTING_VISION_API_TOKEN,
    SERVICE_SETTING_VISION_LOCAL_API_URL,
    SERVICE_SETTING_VISION_CLOUD_API_URL,
    SERVICE_SETTING_TELEGRAM_BOT_TOKEN,
    SERVICE_SETTING_TELEGRAM_CHAT_ID,
)
_ALLOWED_FULL_SCAN_INTERVALS = (15, 30, 60, 120, 300)
_ALLOWED_RECHECK_INTERVALS = (5, 10, 15, 30, 60)
_ALLOWED_CONCURRENCY_VALUES = (1, 2, 3, 4, 5)


@dataclass(slots=True, frozen=True)
class ServiceRuntimeSettings:
    """Эффективные runtime-настройки сервиса с учётом БД и env."""

    auto_pause_enabled: bool
    auto_resume_enabled: bool
    auto_resume_available: bool
    observe_only_enabled: bool
    full_scan_interval_seconds: int
    recheck_interval_seconds: int
    full_scan_profile_concurrency: int
    action_worker_concurrency: int
    vision_api_token: str
    vision_local_api_url: str
    vision_cloud_api_url: str
    telegram_bot_token: str
    telegram_chat_id: str
    updated_at: datetime

    @property
    def scan_interval_seconds(self) -> int:
        return self.full_scan_interval_seconds


def _parse_bool(value: str | None, fallback: bool) -> bool:
    if value is None:
        return fallback
    return value.strip().lower() in {"true", "1", "yes", "on"}


def _parse_interval(value: str | None, fallback: int, allowed_values: tuple[int, ...]) -> int:
    candidate = fallback
    if value is not None:
        try:
            candidate = int(value.strip())
        except ValueError:
            candidate = fallback
    return candidate if candidate in allowed_values else fallback


def _parse_concurrency(value: str | None, fallback: int) -> int:
    return _parse_interval(value, fallback, _ALLOWED_CONCURRENCY_VALUES)


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

    fallback_full_scan_interval = (
        base_settings.full_scan_interval_seconds
        if base_settings.full_scan_interval_seconds in _ALLOWED_FULL_SCAN_INTERVALS
        else 60
    )
    fallback_recheck_interval = (
        base_settings.recheck_interval_seconds
        if base_settings.recheck_interval_seconds in _ALLOWED_RECHECK_INTERVALS
        else 15
    )
    fallback_full_scan_profile_concurrency = (
        base_settings.full_scan_profile_concurrency
        if base_settings.full_scan_profile_concurrency in _ALLOWED_CONCURRENCY_VALUES
        else 2
    )
    fallback_action_worker_concurrency = (
        base_settings.action_worker_concurrency
        if base_settings.action_worker_concurrency in _ALLOWED_CONCURRENCY_VALUES
        else 2
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
        full_scan_interval_seconds=_parse_interval(
            stored.get(SERVICE_SETTING_FULL_SCAN_INTERVAL_SECONDS),
            fallback_full_scan_interval,
            _ALLOWED_FULL_SCAN_INTERVALS,
        ),
        recheck_interval_seconds=_parse_interval(
            stored.get(SERVICE_SETTING_RECHECK_INTERVAL_SECONDS),
            fallback_recheck_interval,
            _ALLOWED_RECHECK_INTERVALS,
        ),
        full_scan_profile_concurrency=_parse_concurrency(
            stored.get(SERVICE_SETTING_FULL_SCAN_PROFILE_CONCURRENCY),
            fallback_full_scan_profile_concurrency,
        ),
        action_worker_concurrency=_parse_concurrency(
            stored.get(SERVICE_SETTING_ACTION_WORKER_CONCURRENCY),
            fallback_action_worker_concurrency,
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
            "full_scan_interval_seconds": runtime_settings.full_scan_interval_seconds,
            "recheck_interval_seconds": runtime_settings.recheck_interval_seconds,
            "full_scan_profile_concurrency": runtime_settings.full_scan_profile_concurrency,
            "action_worker_concurrency": runtime_settings.action_worker_concurrency,
            "feature_auto_pause": runtime_settings.auto_pause_enabled,
            "feature_auto_resume": runtime_settings.auto_resume_enabled,
            "feature_observe_only": runtime_settings.observe_only_enabled,
        }
    )
