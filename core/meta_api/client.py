# -*- coding: utf-8 -*-
"""MetaApiClient — тонкий Python-клиент над gRPC MetaApiService browser-agent.

Архитектурно: client.py НЕ исполняет HTTP-запросы напрямую. Он шлёт gRPC к
browser-agent, который через page.evaluate(fetch) дёргает Graph API изнутри
активной Vision-сессии. Так Meta видит request с правильными cookies/fingerprint.

Изолирован от BrowserAgentClient (см. § 3.3 плана) — может работать на своём
канале либо на общем (через ctor параметр channel).
"""

from __future__ import annotations

import json
import logging
from typing import Any

import grpc

from clients.python_grpc.v1 import meta_api_pb2, meta_api_pb2_grpc
from core.browser.circuit_breaker import AsyncCircuitBreaker, CircuitOpenError
from core.meta_api.errors import (
    MetaApiError,
    SessionUnavailableError,
    TemporaryError,
    classify_graph_error,
)

logger = logging.getLogger(__name__)

# Дефолтный таймаут одного Graph-вызова. Browser-agent внутри ставит 30с,
# Здесь даём небольшой запас (на gRPC прохождение).
_DEFAULT_TIMEOUT_SECONDS = 35.0
# Token-only health (без сетевого запроса) — быстрый.
_HEALTH_CHECK_TIMEOUT_SECONDS = 10.0
# full_probe делает реальный fetch (browser-agent внутри ставит 8с) — даём запас на gRPC.
_HEALTH_PROBE_TIMEOUT_SECONDS = 15.0


class MetaApiClient:
    """Клиент Marketing API через gRPC к browser-agent.

    Usage:
        client = MetaApiClient(host="localhost", port=50051)
        await client.start()
        try:
            data = await client.execute_graph_call(
                method="GET", endpoint="/me", query_params={}
            )
        finally:
            await client.close()
    """

    def __init__(
        self,
        *,
        host: str = "localhost",
        port: int = 50051,
        channel: grpc.aio.Channel | None = None,
        session_id: str = "",
        circuit_breaker: AsyncCircuitBreaker | None = None,
    ) -> None:
        self._host = host
        self._port = port
        self._external_channel = channel is not None
        self._channel: grpc.aio.Channel | None = channel
        self._stub: meta_api_pb2_grpc.MetaApiServiceStub | None = None
        # session_id="" → browser-agent сам выбирает preferred session
        self.session_id = session_id
        self._circuit_breaker = circuit_breaker or AsyncCircuitBreaker(
            name="meta-api",
            failure_threshold=3,
            recovery_timeout=60.0,
        )

    async def start(self) -> None:
        """Открыть свой gRPC канал (если не передан извне)."""
        if self._channel is None:
            self._channel = grpc.aio.insecure_channel(
                f"{self._host}:{self._port}",
                options=[
                    ("grpc.max_receive_message_length", 50 * 1024 * 1024),
                    ("grpc.max_send_message_length", 50 * 1024 * 1024),
                ],
            )
            logger.info("MetaApiClient gRPC канал открыт: %s:%d", self._host, self._port)
        self._stub = meta_api_pb2_grpc.MetaApiServiceStub(self._channel)

    async def close(self) -> None:
        """Закрыть канал — только если он наш собственный."""
        if self._channel and not self._external_channel:
            await self._channel.close()
            logger.info("MetaApiClient gRPC канал закрыт")
        self._channel = None
        self._stub = None

    async def __aenter__(self) -> MetaApiClient:
        await self.start()
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    # ====================== Health ======================

    async def check_health(self, *, full_probe: bool = False) -> dict[str, Any]:
        """CheckMetaApiHealth — статус канала Marketing API для health_watchdog.

        Token-only режим (full_probe=False, дефолт) — дёшево: только URL + наличие
        EAA-токена в DOM, без сетевых запросов. Для частых проверок.

        full_probe=True — browser-agent дополнительно делает РЕАЛЬНЫЙ GET /me?fields=id
        тем же page.evaluate(fetch), что и auto-stop pause_ad. Ловит инцидент 2026-06-19:
        token-only возвращал healthy=true при мёртвом сетевом канале (Failed to fetch).

        Возвращает dict: healthy, current_url, token_present, token_length, detail +
        probe_performed, probe_ok, probe_status_code, probe_duration_ms, probe_detail.
        Не бросает на unhealthy — это просто статус.
        """
        if self._stub is None:
            raise RuntimeError("MetaApiClient не запущен: вызови await start()")
        req = meta_api_pb2.CheckMetaApiHealthRequest(
            session_id=self.session_id,
            full_probe=full_probe,
        )
        timeout = _HEALTH_PROBE_TIMEOUT_SECONDS if full_probe else _HEALTH_CHECK_TIMEOUT_SECONDS
        try:
            resp = await self._circuit_breaker.call(
                self._stub.CheckMetaApiHealth,
                req,
                timeout=timeout,
            )
        except CircuitOpenError as exc:
            return {
                "healthy": False,
                "current_url": "",
                "token_present": False,
                "token_length": 0,
                "detail": f"circuit_open: {exc}",
                "probe_performed": False,
                "probe_ok": False,
                "probe_status_code": 0,
                "probe_duration_ms": 0,
                "probe_detail": "not_performed",
            }
        return {
            "healthy": bool(resp.healthy),
            "current_url": str(resp.current_url),
            "token_present": bool(resp.token_present),
            "token_length": int(resp.token_length),
            "detail": str(resp.detail),
            "probe_performed": bool(resp.probe_performed),
            "probe_ok": bool(resp.probe_ok),
            "probe_status_code": int(resp.probe_status_code),
            "probe_duration_ms": int(resp.probe_duration_ms),
            "probe_detail": str(resp.probe_detail),
        }

    # ====================== Core: ExecuteGraphCall ======================

    async def execute_graph_call(
        self,
        *,
        method: str,
        endpoint: str,
        query_params: dict[str, str] | None = None,
        body_json: str | dict[str, Any] | None = None,
        timeout_ms: int | None = None,
        ad_account_id: str | None = None,
    ) -> dict[str, Any]:
        """Универсальный Graph API call через активную Vision-сессию.

        Возвращает распарсенный JSON-ответ Meta API (dict).
        Бросает доменное исключение из core.meta_api.errors при ошибке Meta.
        Бросает SessionUnavailableError при недоступности Vision.

        Args:
            method: "GET"/"POST"/"DELETE" (case-insensitive)
            endpoint: путь БЕЗ /vXX.Y, например "/me" или "/act_123/insights"
            query_params: query string / form params
            body_json: тело POST (dict сериализуется в JSON-строку)
            timeout_ms: таймаут одного вызова на стороне browser-agent
            ad_account_id: мульти-кабинет — fetch исполняется из вкладки этого кабинета
                (числовой ID без act_). None/"" → primary-вкладка (legacy).
        """
        if self._stub is None:
            raise RuntimeError("MetaApiClient не запущен: вызови await start()")

        params_map = {str(k): str(v) for k, v in (query_params or {}).items()}
        body_str = ""
        if body_json is not None:
            body_str = body_json if isinstance(body_json, str) else json.dumps(body_json)

        req_kwargs: dict[str, Any] = {
            "session_id": self.session_id,
            "method": method.upper(),
            "endpoint": endpoint,
            "query_params": params_map,
            "body_json": body_str,
            "ad_account_id": (ad_account_id or "").removeprefix("act_"),
        }
        if timeout_ms is not None:
            req_kwargs["timeout_ms"] = int(timeout_ms)

        req = meta_api_pb2.ExecuteGraphCallRequest(**req_kwargs)

        try:
            resp = await self._circuit_breaker.call(
                self._stub.ExecuteGraphCall,
                req,
                timeout=_DEFAULT_TIMEOUT_SECONDS,
            )
        except CircuitOpenError as exc:
            raise SessionUnavailableError(
                f"browser-agent недоступен: {exc}",
                endpoint=endpoint,
            ) from exc
        except grpc.RpcError as exc:  # type: ignore[misc]
            # Семантические ошибки от browser-agent: FAILED_PRECONDITION при отсутствии токена и т.д.
            raise self._grpc_to_meta_error(exc, endpoint=endpoint) from exc

        # browser-agent заполняет error из ответа Meta, если он там есть.
        if resp.HasField("error"):
            err = resp.error
            raise classify_graph_error(
                code=err.code or None,
                subcode=err.subcode or None,
                message=err.message or "",
                endpoint=endpoint,
                fbtrace_id=err.fbtrace_id or None,
            )

        # HTTP 4xx/5xx, но без error блока — экзотика. Считаем PermanentError.
        if resp.status_code >= 400:
            raise classify_graph_error(
                code=None,
                subcode=None,
                message=f"HTTP {resp.status_code} без error блока",
                endpoint=endpoint,
            )

        try:
            return json.loads(resp.response_json) if resp.response_json else {}
        except json.JSONDecodeError as exc:
            raise TemporaryError(
                f"Невалидный JSON в ответе Meta: {exc}",
                endpoint=endpoint,
            ) from exc

    # ====================== Высокоуровневые шорткаты ======================

    async def get_ad_insights(
        self,
        *,
        ad_account_id: str,
        fields: list[str] | tuple[str, ...],
        date_preset: str | None = None,
        since: str | None = None,
        until: str | None = None,
        level: str = "ad",
        filtering: list[dict[str, Any]] | None = None,
        breakdowns: list[str] | None = None,
        limit: int = 25,
        action_attribution_windows: list[str] | tuple[str, ...] = (
            "1d_click",
            "7d_click",
            "1d_view",
        ),
    ) -> dict[str, Any]:
        """GET /{ad_account_id}/insights — обёртка над execute_graph_call.

        Возвращает распарсенный ответ Meta как dict (с ключом 'data' — массив строк).
        ad_account_id — с префиксом "act_".
        """
        params: dict[str, str] = {
            "level": level,
            "fields": ",".join(fields),
            "limit": str(limit),
            "action_attribution_windows": json.dumps(list(action_attribution_windows)),
        }
        if date_preset:
            params["date_preset"] = date_preset
        if since and until:
            params["time_range"] = json.dumps({"since": since, "until": until})
        if filtering:
            params["filtering"] = json.dumps(filtering)
        if breakdowns:
            params["breakdowns"] = ",".join(breakdowns)

        return await self.execute_graph_call(
            method="GET",
            endpoint=f"/{ad_account_id}/insights",
            query_params=params,
        )

    async def list_ad_accounts(
        self,
        *,
        fields: list[str] | tuple[str, ...] = ("id", "name", "account_status", "currency"),
        limit: int = 50,
    ) -> dict[str, Any]:
        """GET /me/adaccounts — список ad accounts текущего пользователя."""
        return await self.execute_graph_call(
            method="GET",
            endpoint="/me/adaccounts",
            query_params={
                "fields": ",".join(fields),
                "limit": str(limit),
            },
        )

    # ====================== внутреннее ======================

    @staticmethod
    def _grpc_to_meta_error(exc: grpc.RpcError, *, endpoint: str) -> MetaApiError:
        """Преобразовать gRPC error из browser-agent в доменное исключение.

        browser-agent возвращает:
        - FAILED_PRECONDITION → token_not_found / session not active
        - UNAVAILABLE → browser-agent упал
        - DEADLINE_EXCEEDED → таймаут
        """
        code = exc.code() if hasattr(exc, "code") else None  # type: ignore[union-attr]
        details = exc.details() if hasattr(exc, "details") else str(exc)  # type: ignore[union-attr]

        if code == grpc.StatusCode.FAILED_PRECONDITION:
            return SessionUnavailableError(
                f"Vision-сессия не готова: {details}",
                endpoint=endpoint,
            )
        if code in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED):
            return TemporaryError(
                f"browser-agent временно недоступен ({code.name if code else '?'}): {details}",
                endpoint=endpoint,
            )
        return TemporaryError(
            f"gRPC error {code.name if code else '?'}: {details}",
            endpoint=endpoint,
        )
