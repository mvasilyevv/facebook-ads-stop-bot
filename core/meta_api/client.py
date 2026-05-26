# -*- coding: utf-8 -*-
"""Высокоуровневый async-клиент Marketing API с retry-логикой и circuit-breaker.

Оборачивает clients.python_grpc.meta_api_client.MetaApiClient (низкоуровневый
gRPC-клиент). Добавляет:
- Retry с exponential backoff и классификацией ошибок
- Circuit-breaker (3 фейла → OPEN на 60 сек) через AsyncCircuitBreaker
- Логирование latency, retry, финальных ошибок (без access_token / body_json)
- Удобный API: get_insights(), pause_entity(), set_budget(), и др.

Используется в:
- core/meta_api/insights/fetcher.py
- apps/meta_api_worker (исполнение mutations)
- core/ai_assistant/tools/meta/* (READ-tools)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import TYPE_CHECKING

from clients.python_grpc.meta_api_client import (
    MetaApiClient as _LowLevelClient,
)
from clients.python_grpc.meta_api_client import (
    MetaApiError,
    MetaApiHealth,
)
from core.browser.circuit_breaker import AsyncCircuitBreaker, CircuitOpenError

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

# Таймауты backoff (секунды)
_TRANSIENT_BACKOFF_BASE = 5.0  # 5 → 10 → 20 для transient-ошибок
_RATE_LIMIT_BACKOFF_FACTOR = 30.0  # 30 * attempt для rate-limit


def _is_transient(err: MetaApiError) -> bool:
    """Сетевые / временные ошибки — кандидаты на retry.

    code=2 → API Service Exception (нет ресурсов на стороне Meta),
    code=-1...-3 → внутренние коды browser-agent (сессия жива, но fetch упал).
    """
    return err.code == 2 or err.code in (-1, -2, -3)


def _is_invalid_params(err: MetaApiError) -> bool:
    """Некорректные параметры запроса — retry бессмысленен."""
    return err.code in (1, 100)


class MetaApiHighLevelClient:
    """Высокоуровневый async-клиент Marketing API с retry/circuit-breaker/логированием.

    Оборачивает clients.python_grpc.meta_api_client.MetaApiClient. Используется в:
    - core/meta_api/insights/fetcher.py
    - apps/meta_api_worker (для исполнения mutations)
    - core/ai_assistant/tools/meta/* (READ-tools)
    """

    def __init__(
        self,
        grpc_host: str = "localhost",
        grpc_port: int = 50051,
        *,
        max_attempts: int = 3,
        circuit_breaker: AsyncCircuitBreaker | None = None,
    ) -> None:
        self._low_level = _LowLevelClient(grpc_host=grpc_host, grpc_port=grpc_port)
        self._max_attempts = max_attempts
        # По умолчанию — 3 фейла → OPEN на 60 сек
        self._circuit_breaker = circuit_breaker or AsyncCircuitBreaker(
            name="meta-api",
            failure_threshold=3,
            recovery_timeout=60.0,
        )

    async def start(self) -> None:
        """Открыть gRPC-канал низкоуровневого клиента."""
        await self._low_level.start()

    async def close(self) -> None:
        """Закрыть gRPC-канал."""
        await self._low_level.close()

    async def __aenter__(self) -> MetaApiHighLevelClient:
        await self.start()
        return self

    async def __aexit__(self, *args: object) -> None:
        await self.close()

    # ── Низкоуровневая обёртка с retry ─────────────────────────────────────

    async def execute(
        self,
        method: str,
        endpoint: str,
        query_params: dict[str, str] | None = None,
        *,
        body_json: str | None = None,
        timeout_ms: int | None = None,
        session_id: str = "",
        initiated_by: str = "unknown",
    ) -> dict:
        """Выполнить произвольный Marketing API запрос с retry-логикой.

        Args:
            method: "GET", "POST", "DELETE"
            endpoint: путь без host и API version, напр. "/me" или "/act_X/insights"
            query_params: query-параметры
            body_json: JSON-тело для POST (не логируется — может содержать креативы)
            timeout_ms: таймаут в миллисекундах
            session_id: ID browser-agent сессии
            initiated_by: строка аудита (user id / worker name / "unknown")

        Returns:
            Распарсенный dict из response.response

        Raises:
            MetaApiError: после исчерпания попыток или при неповторяемой ошибке
            CircuitOpenError: если circuit-breaker открыт
        """
        last_error: MetaApiError | None = None

        for attempt in range(1, self._max_attempts + 1):
            t0 = time.monotonic()
            try:
                result = await self._circuit_breaker.call(
                    self._low_level.execute_graph_call,
                    method,
                    endpoint,
                    query_params,
                    body_json=body_json,
                    timeout_ms=timeout_ms,
                    session_id=session_id,
                )
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                logger.info(
                    "Meta API %s %s → %d мс [попытка %d/%d, инициатор=%s]",
                    method,
                    endpoint,
                    elapsed_ms,
                    attempt,
                    self._max_attempts,
                    initiated_by,
                )
                return result.response

            except CircuitOpenError:
                # Circuit-breaker открыт — сразу пробрасываем, не retry
                logger.warning(
                    "Meta API %s %s: circuit-breaker открыт [инициатор=%s]",
                    method,
                    endpoint,
                    initiated_by,
                )
                raise

            except MetaApiError as err:
                elapsed_ms = int((time.monotonic() - t0) * 1000)
                last_error = err

                # Токен умер — нужна перезагрузка Vision-сессии, retry бессмысленен
                if err.is_token_invalidated:
                    logger.error(
                        "Meta API %s %s: токен инвалидирован (code=%d). "
                        "Требуется перезагрузка Vision-сессии [инициатор=%s]",
                        method,
                        endpoint,
                        err.code,
                        initiated_by,
                    )
                    raise

                # Сессия упала — то же
                if err.is_session_dead:
                    logger.error(
                        "Meta API %s %s: Vision-сессия мертва (code=%d) [инициатор=%s]",
                        method,
                        endpoint,
                        err.code,
                        initiated_by,
                    )
                    raise

                # Некорректные параметры — retry бессмысленен
                if _is_invalid_params(err):
                    logger.error(
                        "Meta API %s %s: некорректные параметры (code=%d) [инициатор=%s]",
                        method,
                        endpoint,
                        err.code,
                        initiated_by,
                    )
                    raise

                # Исчерпали попытки
                if attempt >= self._max_attempts:
                    logger.error(
                        "Meta API %s %s: финальная ошибка после %d попыток "
                        "(code=%d, %d мс) [инициатор=%s]",
                        method,
                        endpoint,
                        attempt,
                        err.code,
                        elapsed_ms,
                        initiated_by,
                    )
                    raise

                # Rate limit — backoff = 30 * attempt секунд
                if err.is_rate_limited:
                    backoff = _RATE_LIMIT_BACKOFF_FACTOR * attempt
                    logger.warning(
                        "Meta API %s %s: rate-limit (code=%d), backoff=%.0f сек "
                        "[попытка %d/%d, инициатор=%s]",
                        method,
                        endpoint,
                        err.code,
                        backoff,
                        attempt,
                        self._max_attempts,
                        initiated_by,
                    )
                    await asyncio.sleep(backoff)
                    continue

                # Transient-ошибка — exponential backoff: 5 * 2^(attempt-1)
                if _is_transient(err):
                    backoff = _TRANSIENT_BACKOFF_BASE * (2 ** (attempt - 1))
                    logger.warning(
                        "Meta API %s %s: transient-ошибка (code=%d), backoff=%.0f сек "
                        "[попытка %d/%d, инициатор=%s]",
                        method,
                        endpoint,
                        err.code,
                        backoff,
                        attempt,
                        self._max_attempts,
                        initiated_by,
                    )
                    await asyncio.sleep(backoff)
                    continue

                # Неизвестная ошибка — логируем и поднимаем
                logger.error(
                    "Meta API %s %s: неизвестная ошибка (code=%d, %d мс) [инициатор=%s]",
                    method,
                    endpoint,
                    err.code,
                    elapsed_ms,
                    initiated_by,
                )
                raise

        # Сюда не должны доходить (raise внутри цикла), но для mypy
        if last_error is not None:
            raise last_error
        raise RuntimeError(f"Meta API {method} {endpoint}: неожиданный выход из retry-цикла")

    # ── Удобные методы (read-only) ──────────────────────────────────────────

    async def health(self) -> MetaApiHealth:
        """Проверить готовность Marketing API канала.

        Не выполняет реальных запросов к Meta — проверяет состояние Vision-сессии
        и наличие EAA-токена в странице.
        """
        return await self._low_level.check_health()

    async def me(self) -> dict:
        """GET /me → {id, name} текущей сессии."""
        return await self.execute("GET", "/me")

    async def list_ad_accounts(self, *, fields: str | None = None) -> list[dict]:
        """GET /me/adaccounts → список рекламных кабинетов."""
        params: dict[str, str] = {
            "fields": fields or "id,name,currency,timezone_name,account_status",
            "limit": "100",
        }
        result = await self.execute("GET", "/me/adaccounts", params)
        return result.get("data", [])

    async def get_insights(
        self,
        ad_account_id: str,
        *,
        level: str = "ad",
        fields: list[str] | None = None,
        date_preset: str = "today",
        time_range: dict | None = None,
        filtering: list[dict] | None = None,
        breakdowns: list[str] | None = None,
        limit: int = 100,
        action_attribution_windows: list[str] | None = None,
    ) -> list[dict]:
        """GET /{ad_account_id}/insights — метрики объявлений/кампаний/адсетов.

        По умолчанию action_attribution_windows = ["1d_click","7d_click","1d_view"].
        Meta удалила 7d_view и 28d_view 12 янв 2026 — они не передаются никогда.
        """
        if not ad_account_id.startswith("act_"):
            ad_account_id = f"act_{ad_account_id}"

        # Дефолтные поля insights
        default_fields = [
            "ad_id",
            "ad_name",
            "adset_name",
            "campaign_name",
            "spend",
            "impressions",
            "clicks",
            "cpc",
            "ctr",
            "cpm",
            "frequency",
            "reach",
            "actions",
            "cost_per_action_type",
        ]

        # Attribution windows: явно запрещаем устаревшие 7d_view / 28d_view
        windows = action_attribution_windows or ["1d_click", "7d_click", "1d_view"]
        # Фильтруем на случай если кто-то передал deprecated windows
        windows = [w for w in windows if w not in ("7d_view", "28d_view")]

        params: dict[str, str] = {
            "level": level,
            "fields": ",".join(fields or default_fields),
            "date_preset": date_preset,
            "action_attribution_windows": json.dumps(windows),
            "limit": str(limit),
        }
        if time_range is not None:
            params["time_range"] = json.dumps(time_range)
        if filtering is not None:
            params["filtering"] = json.dumps(filtering)
        if breakdowns is not None:
            params["breakdowns"] = json.dumps(breakdowns)

        result = await self.execute("GET", f"/{ad_account_id}/insights", params)
        return result.get("data", [])

    async def get_entity(
        self,
        entity_id: str,
        *,
        fields: list[str] | None = None,
    ) -> dict:
        """GET /{entity_id}?fields=... — получить объект по ID.

        Универсально работает для Campaign / Adset / Ad.
        """
        params: dict[str, str] = {}
        if fields:
            params["fields"] = ",".join(fields)
        return await self.execute("GET", f"/{entity_id}", params or None)

    # ── Удобные методы (write) ──────────────────────────────────────────────
    # Без БД-outbox (это Этап 5 — apps/meta_api_worker + MetaApiMutationTask).

    async def pause_entity(self, entity_id: str) -> dict:
        """POST /{entity_id} status=PAUSED — поставить на паузу объявление/кампанию."""
        body = json.dumps({"status": "PAUSED"})
        return await self.execute("POST", f"/{entity_id}", body_json=body)

    async def activate_entity(self, entity_id: str) -> dict:
        """POST /{entity_id} status=ACTIVE — активировать объявление/кампанию."""
        body = json.dumps({"status": "ACTIVE"})
        return await self.execute("POST", f"/{entity_id}", body_json=body)

    async def set_budget(
        self,
        entity_id: str,
        *,
        daily_budget_cents: int | None = None,
        lifetime_budget_cents: int | None = None,
    ) -> dict:
        """POST /{entity_id} — изменить бюджет объявления/адсета.

        Значения передаются в центах (Meta API принимает минимальные единицы валюты).
        Передача обоих аргументов одновременно — ошибка Meta API.
        """
        payload: dict = {}
        if daily_budget_cents is not None:
            payload["daily_budget"] = daily_budget_cents
        if lifetime_budget_cents is not None:
            payload["lifetime_budget"] = lifetime_budget_cents
        body = json.dumps(payload)
        return await self.execute("POST", f"/{entity_id}", body_json=body)

    async def duplicate_campaign(
        self,
        campaign_id: str,
        *,
        deep_copy: bool = True,
        rename_options: dict | None = None,
    ) -> dict:
        """POST /{campaign_id}/copies — дублировать кампанию.

        Args:
            campaign_id: ID исходной кампании
            deep_copy: True = скопировать все адсеты и объявления
            rename_options: dict с настройками переименования (опционально)
        """
        payload: dict = {"deep_copy": deep_copy}
        if rename_options is not None:
            payload["rename_options"] = rename_options
        body = json.dumps(payload)
        return await self.execute("POST", f"/{campaign_id}/copies", body_json=body)
