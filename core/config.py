# -*- coding: utf-8 -*-
"""Конфигурация приложения через Pydantic Settings."""

from __future__ import annotations

import logging
from pathlib import Path
from urllib.parse import quote_plus, urlsplit

from pydantic import SecretStr, model_validator
from pydantic_settings import BaseSettings  # type: ignore[import-not-found]

logger = logging.getLogger(__name__)

# Путь к .env — корень проекта; local launcher требует FB_AGENT_PROFILE=local.
_ENV_FILE = Path(__file__).resolve().parents[1] / ".env"


def reveal_secret(value: object) -> str:
    """Достаёт строковое значение секрета: поддерживает `SecretStr` и обычный `str`.

    Секретные поля `Settings` — `SecretStr` (H-6), но юнит-тесты нередко подменяют
    `get_settings()` через `SimpleNamespace`/`MagicMock` с обычными `str`-полями.
    Единая точка распаковки избавляет вызывающий код от `isinstance`-проверок и
    не даёт случайно уйти в прод со строкой `"**********"` вместо токена.
    """
    getter = getattr(value, "get_secret_value", None)
    if callable(getter):
        return getter()
    return value if isinstance(value, str) else str(value)


def safe_url_for_log(url: str) -> str:
    """host:port (+ путь/БД-индекс) без userinfo — для логов (H-6, п.3).

    `database_url`/`redis_url` остаются обычным `str` (не `SecretStr`), т.к. это
    строки подключения, а не голые секреты, — но целиком в лог их класть нельзя:
    userinfo (`user:password@`) может содержать креды. Не логируем raw URL нигде,
    только через эту функцию.
    """
    try:
        parts = urlsplit(url)
    except ValueError:
        return "<url>"
    netloc = parts.hostname or ""
    if parts.port:
        netloc = f"{netloc}:{parts.port}"
    scheme = f"{parts.scheme}://" if parts.scheme else ""
    return f"{scheme}{netloc}{parts.path}"


class Settings(BaseSettings):
    """Настройки приложения, читаются из env-переменных."""

    # --- Postgres ---
    postgres_host: str = "localhost"
    postgres_port: int = 5433
    postgres_db: str = "fb_stop_bot"
    postgres_user: str = "fb_stop_bot"
    postgres_password: SecretStr = SecretStr("fb_stop_bot")

    # --- Telegram ---
    deployment_environment: str = "production"
    telegram_bot_token: SecretStr = SecretStr("")
    telegram_bot_api_origin: str = "https://api.telegram.org"
    # Independent Bot API webhook secret passed in
    # X-Telegram-Bot-Api-Secret-Token. Never derive it from the bot token.
    telegram_webhook_secret: SecretStr = SecretStr("")
    # Dedicated bearer credential used only by the off-host Alertmanager
    # webhook. It is never shared with panel/TMA/API-key authentication.
    alertmanager_webhook_secret: SecretStr = SecretStr("")
    # --- Owner-only panel login (Telegram OIDC Authorization Code + PKCE) ---
    telegram_oidc_client_id: str = ""
    telegram_oidc_client_secret: SecretStr = SecretStr("")
    telegram_oidc_redirect_uri: str = "https://app.adpulse.su/auth/telegram/callback"
    panel_auth_state_ttl_seconds: int = 10 * 60
    panel_auth_ticket_ttl_seconds: int = 60
    panel_auth_session_ttl_seconds: int = 12 * 60 * 60

    # --- Рабочий стол Vision (нативный канал) ---
    # Explicit web-panel owner. Never infer identity from the current owner count.
    desktop_owner_telegram_user_id: int = 0
    # Сюда entrypoint стола публикует адрес брокера, его публичный ключ и ID
    # устройства; api-контейнер монтирует каталог read-only. Секретов в файле
    # нет — это ровно то, что оператор вводит в клиент RustDesk.
    desktop_native_channel_path: Path = Path("/run/fb-agent-desktop-readiness/rustdesk.json")
    # Пароль канала. В разметку страницы он не попадает: ручка запуска отдаёт
    # готовую ссылку владельцу в момент нажатия, а не рендерится в HTML заранее.
    desktop_rustdesk_password: SecretStr = SecretStr("")

    # --- Шифрование (для хранения токенов в БД) ---
    encryption_key: SecretStr = SecretStr("")
    encryption_key_verify: SecretStr = SecretStr("")

    # --- API ---
    api_host: str = "0.0.0.0"
    api_port: int = 8100
    api_key: SecretStr = SecretStr("")
    # Enforce X-API-Key на write-эндпоинтах (POST/PUT/PATCH/DELETE). Secure-by-default:
    # API биндится на 0.0.0.0 + Ingress, поэтому money-управление (выкл авто-стопа,
    # рестарт observer, подтверждение черновиков) закрыто ключом. В production
    # browser проходит Caddy BasicAuth, а Caddy inject'ит API_KEY только upstream;
    # TMA использует отдельно проверяемый Bearer. Ключ никогда не попадает в bundle.
    # Публичные/иначе-защищённые пути (health/metrics, postback, TMA auth) исключены.
    # Тесты выключают флаг через autouse-фикстуру в tests/conftest.py.
    require_api_key: bool = True
    app_timezone: str = "Europe/Kaliningrad"

    # --- Observer ---
    default_observer_interval_seconds: int = 30

    # --- Vision transport (credentials live only in PostgreSQL vision_config) ---
    vision_api_url: str = "http://127.0.0.1:3030"
    vision_cloud_url: str = "https://v1.empr.cloud/api/v1"

    # --- Redis (non-durable cache and wake-up acceleration only) ---
    redis_url: str = "redis://localhost:6380/0"

    # --- Telegram Mini App ---
    tma_session_ttl_seconds: int = 3600
    # M-15 (аудит 2026-07-12): окно приёма initData при /tma/auth. Дефолт валидатора
    # 86400с (24ч) — слишком широкий для replay перехваченного initData. Сужаем до 1ч:
    # клиент шлёт свежий initData при каждом открытии Mini App.
    tma_init_data_max_age_seconds: int = 3600
    web_app_url: str | None = None
    # Dedicated secret for TMA session tokens. Empty means fail-closed; it is
    # never substituted with ENCRYPTION_KEY. Must remain stable across restarts.
    tma_session_secret: SecretStr = SecretStr("")

    # --- Telegram Daily Digest ---
    digest_enabled: bool = True
    digest_hour: int = 9
    digest_timezone: str = "Europe/Moscow"

    # --- AI Assistant (Claude + OpenAI fallback) ---
    ai_diagnostics_enabled: bool = True
    ai_chat_enabled: bool = True
    anthropic_api_key: SecretStr = SecretStr("")
    anthropic_base_url: str = "https://api.claudehub.fun/v1"
    anthropic_model: str = "claude-sonnet-4.6"
    openai_api_key: SecretStr = SecretStr("")
    # OpenAI-совместимый endpoint. Это же — разъём под локальную модель:
    # OPENAI_BASE_URL=http://host:11434/v1 (Ollama/llama.cpp server) подключает
    # self-hosted LLM без правок кода (OpenAIProvider не завязан на домен OpenAI).
    openai_base_url: str = "https://gateway.nekocode.app/andromeda/v1"
    # Имя модели БЕЗ префикса провайдера — гейтвей ждёт голый id (см. GET /models).
    # luna — лёгкий тир 5.6 ($1/$6 за 1M против $5/$30 у sol): tool-calling проверен
    # benchmark 15.07, agent index 75 vs 80 for sol; money paths stay fail-closed.
    # Если ассистент где-то тупит — переключение на gpt-5.6-sol одной строкой в .env.
    openai_model: str = "gpt-5.6-luna"
    ai_diagnostics_cooldown_seconds: int = 1800
    ai_timeout_seconds: int = 20
    ai_max_log_lines: int = 200
    ai_max_tool_iterations: int = 5
    ai_rate_limit_per_hour: int = 30
    # --- Проактивные AI-статусы ---
    # «Пульс кабинета»: выключен по умолчанию — включаем после обкатки комментариев к алертам.
    ai_pulse_enabled: bool = False
    ai_pulse_slots_utc: str = "12:00,16:00,20:00"
    # Кейс куратора: сколько держим стоп-правила после «включить и держать до цены
    # лида» (grace-окно; спенд-кап ~1×CPA живёт в самой рекомендации).
    enable_reco_hold_grace_seconds: int = 3600

    # --- AdSet.pro (внешний MCP-сервер post-click статистики) ---
    # Live verify (2026-05-27): AdSet.pro работает как MCP-сервер `platform-stats-mcp`
    # на https://adset.pro/mcp (JSON-RPC 2.0 + Bearer). REST вида /api/stats/query
    # не существует — host api.adset.pro вообще не резолвится.
    adsetpro_mcp_key: SecretStr = SecretStr("")
    adsetpro_base_url: str = "https://adset.pro"
    adsetpro_timeout_seconds: float = 15.0
    # Bootstrap/import source for the AdSet.pro GET callback secret. Runtime
    # authorization reads the encrypted database singleton only.
    adsetpro_postback_secret: SecretStr = SecretStr("")
    # --- syntx.ai (прямой API генерации креативов, см. core/syntx/) ---
    # JWT из localStorage.auth_token залогиненного syntx (recon_profile), живёт 30 дней.
    # Пусто → клиент возьмёт env SYNTX_AUTH_TOKEN или строку в .env (см. core/syntx/auth.py).
    syntx_auth_token: SecretStr = SecretStr("")
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
    # ВКЛЮЧАТЬ ТОЛЬКО если API стоит за доверенным reverse-proxy (production Caddy),
    # который сам выставляет XFF. По умолчанию False: XFF подделывается любым клиентом,
    # и доверие ему без прокси = обход IP-rate-limit (H7a) → используем реальный TCP-peer.
    trust_proxy_headers: bool = False
    # M-16 (аудит 2026-07-12): число доверенных reverse-proxy перед API. При XFF
    # `<client>, <proxy1>, ..., <proxyN>` реальный клиент — это (N+1)-й элемент СПРАВА
    # (каждый доверенный прокси дописывает peer справа). Левые элементы контролируются
    # клиентом. Брать самый левый (как раньше) = доверять клиент-присланному IP → обход
    # rate-limit даже за корректно настроенным прокси. Дефолт 1 (один ingress).
    trusted_proxy_count: int = 1

    @model_validator(mode="after")
    def _warn_insecure_defaults(self) -> "Settings":
        """Предупреждаем о небезопасных настройках при старте."""
        environment = self.deployment_environment.strip().lower()
        bot_api_origin = self.telegram_bot_api_origin.strip()
        if bot_api_origin != "https://api.telegram.org":
            parsed = urlsplit(bot_api_origin)
            rehearsal_origin = (
                environment == "rehearsal"
                and parsed.scheme == "http"
                and parsed.hostname == "telegram-stub"
                and parsed.port == 18080
                and parsed.path in {"", "/"}
                and not parsed.username
                and not parsed.password
                and not parsed.query
                and not parsed.fragment
            )
            if not rehearsal_origin:
                raise ValueError(
                    "TELEGRAM_BOT_API_ORIGIN must be the official Telegram HTTPS origin; "
                    "only DEPLOYMENT_ENVIRONMENT=rehearsal may use "
                    "http://telegram-stub:18080"
                )
        self.deployment_environment = environment
        self.telegram_bot_api_origin = bot_api_origin.rstrip("/")
        if self.postgres_password.get_secret_value() == self.postgres_db:
            logger.warning(
                "Пароль Postgres совпадает с именем БД — "
                "задайте уникальный POSTGRES_PASSWORD для продакшена"
            )
        if not self.encryption_key.get_secret_value():
            logger.warning("ENCRYPTION_KEY не задан — шифрование токенов в БД не будет работать")
        elif not self.encryption_key_verify.get_secret_value():
            logger.error(
                "ENCRYPTION_KEY_VERIFY не задан — проверка ключа шифрования завершится fail-closed"
            )
        if not self.telegram_bot_token.get_secret_value():
            logger.warning(
                "TELEGRAM_BOT_TOKEN не задан — runtime config bootstrap не запишет "
                "начальный токен; runtime использует только telegram_config"
            )
        elif not self.telegram_webhook_secret.get_secret_value():
            logger.warning("TELEGRAM_WEBHOOK_SECRET не задан — Telegram webhook будет fail-closed")
        if not self.alertmanager_webhook_secret.get_secret_value():
            logger.warning(
                "ALERTMANAGER_WEBHOOK_SECRET не задан — Alertmanager webhook будет fail-closed"
            )
        if not self.api_key.get_secret_value() and self.require_api_key:
            # L6: НЕ генерим эфемерный ключ. Раньше пустой API_KEY → ротирующийся ключ
            # in-memory (свой на каждый из 12 процессов) → Caddy/API clients ловили 401, а
            # честная ветка 503 в ApiKeyAuthMiddleware была мёртвой. Теперь оставляем ключ
            # пустым → middleware вернёт диагностируемый 503 «API_KEY не сконфигурирован».
            logger.error(
                "API_KEY не задан, но REQUIRE_API_KEY=true — write-эндпоинты вернут 503. "
                "Задай стабильный API_KEY в .env (production Caddy inject'ит его upstream) или "
                "REQUIRE_API_KEY=false для локальной разработки."
            )
        return self

    @property
    def database_url(self) -> str:
        """Строка подключения к Postgres для asyncpg."""
        return (
            f"postgresql+asyncpg://{self.postgres_user}:"
            f"{quote_plus(self.postgres_password.get_secret_value())}"
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
