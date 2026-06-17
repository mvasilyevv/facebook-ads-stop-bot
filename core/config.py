# -*- coding: utf-8 -*-
"""Конфигурация приложения через Pydantic Settings."""

from __future__ import annotations

import logging
import secrets
from pathlib import Path
from urllib.parse import quote_plus

from pydantic import model_validator
from pydantic_settings import BaseSettings  # type: ignore[import-not-found]

logger = logging.getLogger(__name__)

# Путь к .env — корень проекта (рядом с run.sh)
_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def _generate_ephemeral_api_key() -> str:
    """Генерирует временный API-ключ in-memory (НЕ пишет в .env).

    Раньше дописывал ключ в .env через open("a") — при изначально пустом API_KEY
    КАЖДЫЙ старт процесса (API + 12 воркеров + скрипты + тесты) добавлял новую строку
    → .env разрастался дублями (наблюдался кейс 131× API_KEY), а «последний» ключ
    менялся при каждом рестарте, ломая X-API-Key фронта (401 на мутациях). Теперь ключ
    эфемерный: для СТАБИЛЬНОГО ключа задать API_KEY в .env явно; для локалки —
    REQUIRE_API_KEY=false.
    """
    logger.warning(
        "API_KEY не задан в .env — сгенерирован ВРЕМЕННЫЙ ключ (меняется при рестарте). "
        "Задай API_KEY в .env для стабильности или REQUIRE_API_KEY=false для локальной разработки."
    )
    return secrets.token_urlsafe(32)


class Settings(BaseSettings):
    """Настройки приложения, читаются из env-переменных."""

    # --- Postgres ---
    postgres_host: str = "localhost"
    postgres_port: int = 5433
    postgres_db: str = "fb_stop_bot"
    postgres_user: str = "fb_stop_bot"
    postgres_password: str = "fb_stop_bot"

    # --- Telegram ---
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # --- Шифрование (для хранения токенов в БД) ---
    encryption_key: str = ""
    encryption_key_verify: str = ""

    # --- API ---
    api_host: str = "0.0.0.0"
    api_port: int = 8100
    api_key: str = ""
    # Enforce X-API-Key на write-эндпоинтах (POST/PUT/PATCH/DELETE). Secure-by-default:
    # API биндится на 0.0.0.0 + Ingress, поэтому money-управление (выкл авто-стопа,
    # рестарт observer, подтверждение черновиков) закрыто ключом. run.sh прокидывает
    # API_KEY → VITE_API_KEY → фронт шлёт X-API-Key. Публичные/иначе-защищённые пути
    # (health/metrics, /api/v1/postback — свой секрет, /api/tma — Bearer) исключены.
    # Тесты выключают флаг через autouse-фикстуру в tests/conftest.py.
    require_api_key: bool = True
    app_timezone: str = "Europe/Kaliningrad"

    # --- Observer ---
    default_observer_interval_seconds: int = 90

    # --- Vision Anti-detect браузер ---
    vision_x_token: str = ""
    vision_api_url: str = "http://127.0.0.1:3030"
    vision_profile_id: str = ""
    vision_auto_restart_on_missing_cdp: bool = True

    # --- Redis (очередь алёртов) ---
    redis_url: str = "redis://localhost:6380/0"
    alerts_queue_enabled: bool = True

    # --- Sentry (опционально) ---
    sentry_dsn: str = ""
    sentry_environment: str = "production"

    # --- Telegram Mini App ---
    tma_session_ttl_seconds: int = 3600
    web_app_url: str | None = None
    # Секрет для подписи TMA-сессионных токенов (itsdangerous). Пусто → фолбэк на
    # encryption_key. Должен быть стабильным между рестартами (иначе токены протухают).
    tma_session_secret: str = ""

    # --- Telegram Daily Digest ---
    digest_enabled: bool = True
    digest_hour: int = 9
    digest_timezone: str = "Europe/Moscow"

    # --- AI Assistant (Claude + OpenAI fallback) ---
    ai_diagnostics_enabled: bool = True
    ai_chat_enabled: bool = True
    anthropic_api_key: str = ""
    anthropic_base_url: str = "https://api.claudehub.fun/v1"
    anthropic_model: str = "claude-sonnet-4.6"
    openai_api_key: str = ""
    openai_base_url: str = "https://gateway.nekocode.app/andromeda/v1"
    openai_model: str = "openai/gpt-5.4-mini"
    ai_diagnostics_cooldown_seconds: int = 1800
    ai_timeout_seconds: int = 20
    ai_max_log_lines: int = 200
    ai_max_tool_iterations: int = 5
    ai_rate_limit_per_hour: int = 30
    # Объяснения причин алертов через AI (приклеиваются к STOP/WARNING в Telegram)
    ai_explain_alerts_enabled: bool = True
    # 8s было мало для сторонних gateway: 💡-объяснение всегда падало по timeout.
    # 20s — компромисс между UX и блокировкой алерта.
    ai_explain_timeout_seconds: float = 20.0

    # --- AdSet.pro (внешний MCP-сервер post-click статистики, см. META_INTEGRATION_PLAN §4.4 / Этап 6) ---
    # Live verify (2026-05-27): AdSet.pro работает как MCP-сервер `platform-stats-mcp`
    # на https://adset.pro/mcp (JSON-RPC 2.0 + Bearer). REST вида /api/stats/query
    # не существует — host api.adset.pro вообще не резолвится.
    adsetpro_mcp_key: str = ""
    adsetpro_base_url: str = "https://adset.pro"
    adsetpro_timeout_seconds: float = 15.0
    # Секрет для аутентификации входящего postback'а от AdSet.pro
    # (header X-Postback-Secret). Пустая строка → endpoint возвращает 503
    # «not configured», чтобы случайно не принимать неавторизованные постбэки.
    adsetpro_postback_secret: str = ""

    # --- syntx.ai (прямой API генерации креативов, см. core/syntx/) ---
    # JWT из localStorage.auth_token залогиненного syntx (recon_profile), живёт 30 дней.
    # Пусто → клиент возьмёт env SYNTX_AUTH_TOKEN или строку в .env (см. core/syntx/auth.py).
    syntx_auth_token: str = ""
    syntx_base_url: str = "https://api.syntx.ai"
    syntx_timeout_seconds: float = 60.0
    syntx_poll_interval_seconds: float = 3.0
    syntx_poll_timeout_seconds: float = 300.0
    # Дефолтные модели (ai_name / model_type). Видео — на будущее (выключено).
    syntx_default_image_ai: str = "sora-images"
    syntx_default_image_model: str = "gpt-image-2"
    # Правка картинок (Тир 1): Nano Banana = faithful instruction-edit (не flux-kontext).
    syntx_default_edit_ai: str = "banana"
    syntx_default_edit_model: str = "banana3"
    syntx_default_video_ai: str = "kling"
    syntx_default_video_model: str = "kling_image2video"

    # --- Исходящий postback (форвард конверсий во внешнюю систему/трекер) ---
    # URL-шаблон с макросами {click_id}/{event_type}/{goal}/{revenue}/{payout}/
    # {currency}/{fb_ad_id}/{country}. Пустой → отправка отключена (см. tracker_outgoing_enabled).
    tracker_outgoing_postback_url: str = ""
    tracker_outgoing_enabled: bool = False
    tracker_outgoing_method: str = "GET"
    tracker_outgoing_timeout_seconds: float = 10.0

    # --- Frontend (CORS) ---
    # Origin основного фронта (например, http://localhost:5173). None → CORS не подключаем.
    frontend_origin: str | None = None

    # --- Dev Tools ---
    # Включает /tools/* endpoints (работа с локальной ФС, открытие Finder).
    # По умолчанию выключено — в проде эти операции бессмысленны и опасны.
    # Для локальной разработки: DEV_TOOLS_ENABLED=true в .env
    dev_tools_enabled: bool = False

    # --- Reverse-proxy ---
    # Доверять заголовку X-Forwarded-For для определения client IP (rate-limit и т.п.).
    # ВКЛЮЧАТЬ ТОЛЬКО если API стоит за доверенным reverse-proxy (k8s-ingress/nginx),
    # который сам выставляет XFF. По умолчанию False: XFF подделывается любым клиентом,
    # и доверие ему без прокси = обход IP-rate-limit (H7a) → используем реальный TCP-peer.
    trust_proxy_headers: bool = False

    @model_validator(mode="after")
    def _warn_insecure_defaults(self) -> "Settings":
        """Предупреждаем о небезопасных настройках при старте."""
        if self.postgres_password == self.postgres_db:
            logger.warning(
                "Пароль Postgres совпадает с именем БД — "
                "задайте уникальный POSTGRES_PASSWORD для продакшена"
            )
        if not self.encryption_key:
            logger.warning("ENCRYPTION_KEY не задан — шифрование токенов в БД не будет работать")
        if not self.telegram_bot_token:
            logger.warning("TELEGRAM_BOT_TOKEN не задан — Telegram-бот не будет работать")
        if not self.api_key:
            self.api_key = _generate_ephemeral_api_key()
        return self

    @property
    def database_url(self) -> str:
        """Строка подключения к Postgres для asyncpg."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{quote_plus(self.postgres_password)}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    model_config = {"env_file": ".env", "extra": "ignore"}


_settings: Settings | None = None


def get_settings() -> Settings:
    """Ленивый синглтон настроек."""
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings
