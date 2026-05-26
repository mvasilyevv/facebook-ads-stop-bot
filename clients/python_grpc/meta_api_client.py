# -*- coding: utf-8 -*-
"""gRPC клиент для MetaApiService.

Архитектура: Marketing API вызовы исполняются изнутри активной Playwright-сессии
browser-agent через page.evaluate(fetch). Этот клиент — тонкая обёртка над gRPC-вызовом.

Не путать с BrowserAgentClient (browser session + scanner + creator) — это отдельный
сервис с собственным каналом. Использует тот же gRPC endpoint (порт 50051), но
другой service definition.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import grpc

from clients.python_grpc.v1 import meta_api_pb2, meta_api_pb2_grpc

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 35.0  # browser-agent timeout по умолчанию 30s + overhead


class MetaApiError(RuntimeError):
    """Ошибка вызова Marketing API. Включает Meta error code/subcode/type."""

    def __init__(
        self,
        message: str,
        *,
        code: int = 0,
        subcode: int = 0,
        type_: str = "",
        fbtrace_id: str = "",
        status_code: int = 0,
    ) -> None:
        self.code = code
        self.subcode = subcode
        self.type = type_
        self.fbtrace_id = fbtrace_id
        self.status_code = status_code
        super().__init__(message)

    @property
    def is_token_invalidated(self) -> bool:
        """Code 190 + subcode 463/460 → токен умер, нужна перезагрузка Vision-сессии."""
        return self.code == 190

    @property
    def is_rate_limited(self) -> bool:
        """Code 17 = User request limit reached, code 4 = Application request limit."""
        return self.code in (4, 17, 32, 613)

    @property
    def is_session_dead(self) -> bool:
        """Ситуация, когда страница недоступна, токен не найден, сессия отвалилась."""
        return self.code in (-1, -2, -3)  # наши кастомные коды из browser-agent


@dataclass
class GraphCallResult:
    """Результат вызова Marketing API."""

    status_code: int
    response: dict[str, Any] = field(default_factory=dict)
    duration_ms: int = 0
    raw_json: str = ""


@dataclass
class MetaApiHealth:
    """Состояние Marketing API канала."""

    healthy: bool
    current_url: str
    token_present: bool
    token_length: int
    detail: str


class MetaApiClient:
    """gRPC клиент для MetaApiService.

    Usage:
        client = MetaApiClient(grpc_host="localhost", grpc_port=50051)
        await client.start()
        try:
            health = await client.check_health()
            if not health.healthy:
                raise RuntimeError(f"Marketing API недоступен: {health.detail}")

            result = await client.execute_graph_call("GET", "/me", {})
            print(result.response)
        finally:
            await client.close()
    """

    def __init__(
        self,
        grpc_host: str = "localhost",
        grpc_port: int = 50051,
    ) -> None:
        self._grpc_host = grpc_host
        self._grpc_port = grpc_port
        self._channel: grpc.aio.Channel | None = None
        self._stub: meta_api_pb2_grpc.MetaApiServiceStub | None = None

    async def start(self) -> None:
        """Открыть gRPC-канал."""
        if self._channel is not None:
            return
        self._channel = grpc.aio.insecure_channel(f"{self._grpc_host}:{self._grpc_port}")
        self._stub = meta_api_pb2_grpc.MetaApiServiceStub(self._channel)

    async def close(self) -> None:
        """Закрыть gRPC-канал."""
        if self._channel is not None:
            await self._channel.close()
        self._channel = None
        self._stub = None

    async def __aenter__(self) -> MetaApiClient:
        await self.start()
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    async def check_health(self, *, session_id: str = "") -> MetaApiHealth:
        """Проверить готовность Marketing API канала.

        Лёгкий вызов — не делает реальных запросов к Meta. Проверяет:
        - живая ли Vision-сессия
        - на правильной ли странице (Ads Manager)
        - извлекается ли EAA-токен

        Не выбрасывает исключение при unhealthy состоянии — возвращает структуру
        с детальным состоянием, чтобы вызывающий код мог принять решение.
        """
        if self._stub is None:
            raise RuntimeError("MetaApiClient не запущен. Вызовите start() сначала.")

        request = meta_api_pb2.CheckMetaApiHealthRequest(session_id=session_id)
        response = await self._stub.CheckMetaApiHealth(
            request,
            timeout=_DEFAULT_TIMEOUT_SECONDS,
        )

        return MetaApiHealth(
            healthy=bool(response.healthy),
            current_url=str(response.current_url),
            token_present=bool(response.token_present),
            token_length=int(response.token_length),
            detail=str(response.detail),
        )

    async def execute_graph_call(
        self,
        method: str,
        endpoint: str,
        query_params: dict[str, str] | None = None,
        *,
        body_json: str | None = None,
        timeout_ms: int | None = None,
        session_id: str = "",
        raise_on_error: bool = True,
    ) -> GraphCallResult:
        """Исполнить произвольный Marketing API запрос.

        Args:
            method: "GET", "POST", "DELETE"
            endpoint: путь без host и без API version. Примеры: "/me", "/act_X/insights"
            query_params: query-параметры (для GET) или form-параметры (для POST)
            body_json: JSON-тело для POST/PUT (опционально)
            timeout_ms: таймаут в миллисекундах (по умолчанию 30000)
            session_id: ID конкретной browser-сессии (по умолчанию preferred)
            raise_on_error: если True (default) — бросает MetaApiError при error в ответе

        Returns:
            GraphCallResult с распарсенным response

        Raises:
            MetaApiError: если raise_on_error=True и Meta вернула error
            grpc.aio.AioRpcError: при сетевых проблемах с browser-agent
        """
        if self._stub is None:
            raise RuntimeError("MetaApiClient не запущен. Вызовите start() сначала.")

        # Конвертация всех значений query_params в строки (proto map<string, string>).
        normalized_params: dict[str, str] = {}
        for key, value in (query_params or {}).items():
            if value is None:
                continue
            if isinstance(value, (list, tuple, dict)):
                normalized_params[str(key)] = json.dumps(value, separators=(",", ":"))
            else:
                normalized_params[str(key)] = str(value)

        request_kwargs: dict[str, Any] = {
            "session_id": session_id,
            "method": method.upper(),
            "endpoint": endpoint if endpoint.startswith("/") else f"/{endpoint}",
            "query_params": normalized_params,
        }
        if body_json is not None:
            request_kwargs["body_json"] = body_json
        if timeout_ms is not None:
            request_kwargs["timeout_ms"] = int(timeout_ms)

        request = meta_api_pb2.ExecuteGraphCallRequest(**request_kwargs)

        # gRPC timeout = browser-agent timeout + запас на сериализацию.
        effective_timeout_ms = timeout_ms if timeout_ms is not None else 30_000
        grpc_timeout = (effective_timeout_ms / 1000.0) + 5.0

        response = await self._stub.ExecuteGraphCall(request, timeout=grpc_timeout)

        raw = response.response_json or "{}"
        try:
            parsed = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            parsed = {"raw": raw}

        result = GraphCallResult(
            status_code=int(response.status_code),
            response=parsed if isinstance(parsed, dict) else {"data": parsed},
            duration_ms=int(response.duration_ms),
            raw_json=raw,
        )

        if response.HasField("error") and raise_on_error:
            err = response.error
            raise MetaApiError(
                err.message or "Marketing API вернул ошибку",
                code=int(err.code),
                subcode=int(err.subcode),
                type_=str(err.type),
                fbtrace_id=str(err.fbtrace_id),
                status_code=result.status_code,
            )

        return result

    # ── Удобные обёртки для часто используемых вызовов ─────────────────────

    async def get_me(self, *, session_id: str = "") -> dict[str, Any]:
        """GET /me — вернуть user_id, name текущей сессии."""
        result = await self.execute_graph_call("GET", "/me", {}, session_id=session_id)
        return result.response

    async def list_ad_accounts(
        self,
        *,
        fields: str = "id,name,currency,timezone_name,account_status",
        session_id: str = "",
    ) -> list[dict[str, Any]]:
        """GET /me/adaccounts — список рекламных кабинетов."""
        result = await self.execute_graph_call(
            "GET",
            "/me/adaccounts",
            {"fields": fields, "limit": "100"},
            session_id=session_id,
        )
        return result.response.get("data", [])

    async def get_insights(
        self,
        ad_account_id: str,
        *,
        level: str = "ad",
        fields: list[str] | None = None,
        date_preset: str = "today",
        limit: int = 100,
        session_id: str = "",
    ) -> list[dict[str, Any]]:
        """GET /act_X/insights — метрики объявлений.

        ad_account_id — с префиксом act_ или без (будет добавлен автоматически).
        """
        if not ad_account_id.startswith("act_"):
            ad_account_id = f"act_{ad_account_id}"

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

        params = {
            "level": level,
            "fields": ",".join(fields or default_fields),
            "date_preset": date_preset,
            # Явно фиксируем attribution windows. 7d_view и 28d_view удалены Meta
            # 12 января 2026 — silent failure возвращает пустые данные.
            "action_attribution_windows": json.dumps(["1d_click", "7d_click", "1d_view"]),
            "limit": str(limit),
        }

        result = await self.execute_graph_call(
            "GET",
            f"/{ad_account_id}/insights",
            params,
            session_id=session_id,
        )
        return result.response.get("data", [])
