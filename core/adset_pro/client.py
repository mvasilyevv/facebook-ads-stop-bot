# -*- coding: utf-8 -*-
"""AdsetProClient — минимальный async REST-клиент трекера AdSet.pro.

Архитектура (см. META_INTEGRATION_PLAN.md §4.4 / Этап 6):
- AdSet.pro — независимый от Vision канал (post-click данные: FTD, hold, redep).
- Запросы шлются стандартным httpx.AsyncClient — не через browser-agent.
- API key (MCP) подставляется в заголовок Authorization. Имя заголовка/схема
  токена могут отличаться у реального AdSet.pro — см. TODO в _build_headers.
- Retry на transient errors через tenacity (exponential backoff).
- На Этапе 6 поверх этого клиента появятся: postback FastAPI endpoint, ingest в БД,
  расширение RuleContext конверсиями. Сейчас — только клиент, без БД и без endpoint'а.
"""

from __future__ import annotations

import logging
from datetime import date
from types import TracebackType
from typing import Any

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.adset_pro.errors import (
    AdsetProError,
    TemporaryError,
    classify_http_error,
)
from core.adset_pro.schemas import (
    ConversionRow,
    StatsQueryRequest,
    StatsQueryResponse,
)
from core.config import get_settings

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 15.0
_HEALTH_PATH = "/ping"
# TODO(stage-6): уточнить реальный endpoint AdSet.pro для статистики — пока
# держим /api/stats/query как заявлено в META_INTEGRATION_PLAN.md §4.4.
_STATS_QUERY_PATH = "/api/stats/query"


def _build_headers(api_key: str) -> dict[str, str]:
    """Сформировать заголовки запроса.

    TODO(stage-6): подтвердить схему авторизации у AdSet.pro — Bearer / X-API-Key /
    X-MCP-Key. По умолчанию используем "Authorization: Bearer ..." как самый
    распространённый вариант для PAT/MCP токенов.
    """
    return {
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


class AdsetProClient:
    """Async REST-клиент AdSet.pro.

    Параметры берутся из core/config.Settings (adsetpro_*), либо переопределяются
    явно через ctor (удобно для тестов).

    Usage:
        async with AdsetProClient() as client:
            ok = await client.health_check()
            stats = await client.query_stats(StatsQueryRequest(since=..., until=...))
            rows = await client.list_conversions(since=..., until=...)
    """

    def __init__(
        self,
        *,
        api_key: str | None = None,
        base_url: str | None = None,
        timeout_seconds: float | None = None,
        http_client: httpx.AsyncClient | None = None,
        max_retries: int = 3,
    ) -> None:
        settings = get_settings()
        self._api_key = api_key if api_key is not None else settings.adsetpro_mcp_key
        self._base_url = (base_url or settings.adsetpro_base_url).rstrip("/")
        self._timeout_seconds = (
            timeout_seconds
            if timeout_seconds is not None
            else getattr(settings, "adsetpro_timeout_seconds", _DEFAULT_TIMEOUT_SECONDS)
        )
        self._external_client = http_client is not None
        self._http: httpx.AsyncClient | None = http_client
        self._max_retries = max(1, max_retries)

    # ====================== lifecycle ======================

    async def start(self) -> None:
        """Открыть HTTP-клиент (если не передан извне)."""
        if self._http is None:
            self._http = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout_seconds,
                headers=_build_headers(self._api_key),
            )
            logger.info("AdsetProClient запущен: %s", self._base_url)

    async def close(self) -> None:
        """Закрыть HTTP-клиент — только если он наш собственный."""
        if self._http is not None and not self._external_client:
            await self._http.aclose()
            logger.info("AdsetProClient закрыт")
        self._http = None

    async def __aenter__(self) -> AdsetProClient:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    # ====================== public API ======================

    async def health_check(self) -> bool:
        """Лёгкий пинг AdSet.pro. Возвращает True/False, не бросает.

        TODO(stage-6): подтвердить реальный health-endpoint — сейчас пробуем GET /ping.
        Любой не-2xx или сетевой сбой → False.
        """
        if self._http is None:
            raise RuntimeError("AdsetProClient не запущен: вызови await start()")
        try:
            resp = await self._http.get(_HEALTH_PATH)
        except httpx.HTTPError as exc:
            logger.debug("AdsetProClient health_check: сетевая ошибка %s", exc)
            return False
        return 200 <= resp.status_code < 300

    async def query_stats(self, query: StatsQueryRequest) -> StatsQueryResponse:
        """POST /api/stats/query — выборка статистики/конверсий.

        TODO(stage-6): уточнить реальный путь и формат payload — endpoint
        прописан best-effort из META_INTEGRATION_PLAN.md §4.4.
        """
        payload = query.to_payload()
        data = await self._request_json("POST", _STATS_QUERY_PATH, json=payload)
        return StatsQueryResponse.from_api_payload(data)

    async def list_conversions(
        self,
        *,
        since: date,
        until: date,
        ad_id: str | None = None,
    ) -> list[ConversionRow]:
        """Сахар над query_stats: вернуть список ConversionRow за интервал.

        Если ad_id указан — фильтр по ext_sub6 на стороне AdSet.pro.
        """
        request = StatsQueryRequest(since=since, until=until, ad_id=ad_id)
        response = await self.query_stats(request)
        return list(response.rows)

    # ====================== internals ======================

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Общий путь HTTP-запроса с retry на TemporaryError и сетевых ошибках.

        Permanent-ошибки (auth/not found) не ретраятся — пробрасываются сразу.
        """
        if self._http is None:
            raise RuntimeError("AdsetProClient не запущен: вызови await start()")

        retrying = AsyncRetrying(
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=1.0, min=1.0, max=8.0),
            retry=retry_if_exception_type((TemporaryError, httpx.TransportError)),
            reraise=True,
        )

        async for attempt in retrying:
            with attempt:
                try:
                    resp = await self._http.request(
                        method,
                        path,
                        json=json,
                        params=params,
                    )
                except httpx.TransportError:
                    logger.warning(
                        "AdsetProClient: сетевая ошибка на %s %s — будет retry",
                        method,
                        path,
                    )
                    raise
                return self._parse_response(resp, endpoint=path)

        # На случай если AsyncRetrying ничего не вернёт (теоретически невозможно).
        raise AdsetProError("AdsetProClient: retry loop finished without result", endpoint=path)

    @staticmethod
    def _parse_response(resp: httpx.Response, *, endpoint: str) -> dict[str, Any]:
        """Разобрать HTTP-ответ. Не-2xx → подходящий AdsetProError."""
        if 200 <= resp.status_code < 300:
            if not resp.content:
                return {}
            try:
                data = resp.json()
            except ValueError as exc:
                raise TemporaryError(
                    f"Невалидный JSON в ответе AdSet.pro: {exc}",
                    status_code=resp.status_code,
                    endpoint=endpoint,
                    response_body=resp.text[:500],
                ) from exc
            if not isinstance(data, dict):
                # AdSet.pro может вернуть список верхнего уровня — оборачиваем.
                return {"data": data}
            return data

        body = resp.text[:500]
        message = f"AdSet.pro {resp.status_code} на {endpoint}: {body}"
        raise classify_http_error(
            resp.status_code,
            message,
            endpoint=endpoint,
            response_body=body,
        )
