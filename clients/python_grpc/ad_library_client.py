# -*- coding: utf-8 -*-
"""gRPC клиент для AdLibraryService.

Архитектура: вызовы Ad Library исполняются изнутри активной Playwright-сессии
browser-agent — в новой вкладке того же BrowserContext, где залогинен Facebook.
Это позволяет видеть рекламу в любой стране (Африка/LatAm/Турция) без proxy/токена.

Не путать с MetaApiClient (Marketing API через page.evaluate) — это отдельный
сервис на том же gRPC endpoint (порт 50051), но другой service definition.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

import grpc

from clients.python_grpc.v1 import ad_library_pb2, ad_library_pb2_grpc

logger = logging.getLogger(__name__)

# Ad Library грузится медленнее Marketing API: SPA, render, серия GraphQL.
_DEFAULT_TIMEOUT_SECONDS = 75.0


class AdLibraryError(RuntimeError):
    """Ошибка вызова Ad Library: нет сессии, GraphQL не пришёл, страница упала."""

    def __init__(
        self,
        message: str,
        *,
        code: int = 0,
        type_: str = "",
    ) -> None:
        self.code = code
        self.type = type_
        super().__init__(message)

    @property
    def is_session_missing(self) -> bool:
        """Vision-сессия не найдена или browser отключился."""
        return self.type in ("SessionNotFound", "PageError") and "не найден" in str(self)

    @property
    def is_blocked_by_meta(self) -> bool:
        """Meta отдала challenge или ответ без ads → возможно нужна релогин/прокси."""
        return self.type == "NoGraphQLResponse"


@dataclass
class AdLibrarySearchResult:
    """Результат поиска в Ad Library."""

    ad_count: int
    ads: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: int = 0
    pages_fetched: int = 0
    raw_json: str = ""


@dataclass
class AdLibraryQueryResult:
    """Результат одного query в batch'е."""

    query: str
    ad_count: int
    ads: list[dict[str, Any]] = field(default_factory=list)
    duration_ms: int = 0
    pages_fetched: int = 0
    error_type: str = ""
    error_message: str = ""


@dataclass
class AdLibraryBatchResult:
    """Результат batch-поиска."""

    results: list[AdLibraryQueryResult] = field(default_factory=list)
    total_duration_ms: int = 0


@dataclass
class AdLibraryHealth:
    """Состояние Ad Library канала."""

    healthy: bool
    detail: str


class AdLibraryClient:
    """gRPC клиент для AdLibraryService.

    Usage:
        client = AdLibraryClient(grpc_host="localhost", grpc_port=50051)
        await client.start()
        try:
            health = await client.check_health()
            if not health.healthy:
                raise RuntimeError(f"Ad Library недоступен: {health.detail}")

            result = await client.search_ads(country="KE", query="Chicken Road 2")
            for ad in result.ads:
                print(ad.get("page", {}).get("name"), ad.get("ad_archive_id"))
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
        self._stub: ad_library_pb2_grpc.AdLibraryServiceStub | None = None

    async def start(self) -> None:
        """Открыть gRPC-канал."""
        if self._channel is not None:
            return
        self._channel = grpc.aio.insecure_channel(f"{self._grpc_host}:{self._grpc_port}")
        self._stub = ad_library_pb2_grpc.AdLibraryServiceStub(self._channel)

    async def close(self) -> None:
        """Закрыть gRPC-канал."""
        if self._channel is not None:
            await self._channel.close()
        self._channel = None
        self._stub = None

    async def __aenter__(self) -> AdLibraryClient:
        await self.start()
        return self

    async def __aexit__(self, *_args: object) -> None:
        await self.close()

    async def check_health(self, *, session_id: str = "") -> AdLibraryHealth:
        """Проверить готовность Ad Library канала.

        Лёгкий вызов — не делает реальных запросов к Meta. Проверяет:
        - есть ли активная Vision-сессия
        - подключен ли browser

        Не выбрасывает исключение при unhealthy — возвращает структуру со статусом.
        """
        if self._stub is None:
            raise RuntimeError("AdLibraryClient не запущен. Вызовите start() сначала.")

        request = ad_library_pb2.CheckAdLibraryHealthRequest(session_id=session_id)
        response = await self._stub.CheckAdLibraryHealth(
            request,
            timeout=_DEFAULT_TIMEOUT_SECONDS,
        )

        return AdLibraryHealth(
            healthy=bool(response.healthy),
            detail=str(response.detail),
        )

    async def search_ads(
        self,
        *,
        country: str,
        query: str,
        active_status: str = "active",
        ad_type: str = "all",
        search_type: str = "keyword_unordered",
        max_pages: int | None = None,
        page_size: int | None = None,
        timeout_ms: int | None = None,
        session_id: str = "",
        raise_on_error: bool = True,
    ) -> AdLibrarySearchResult:
        """Поиск рекламы в Ad Library.

        Args:
            country: ISO-2 код страны ("KE", "CD", "MZ", "GH", "TR", "IT", ...)
            query: keyword (например "Chicken Road 2")
            active_status: "active" / "inactive" / "all"
            ad_type: "all" / "political_and_issue_ads"
            timeout_ms: таймаут в миллисекундах (по умолчанию 60_000)
            session_id: ID конкретной browser-сессии (по умолчанию preferred)
            raise_on_error: бросать AdLibraryError при ошибке

        Returns:
            AdLibrarySearchResult со списком ads (raw dict из GraphQL)

        Raises:
            AdLibraryError: если raise_on_error=True и в response есть error
            grpc.aio.AioRpcError: при сетевых проблемах с browser-agent
        """
        if self._stub is None:
            raise RuntimeError("AdLibraryClient не запущен. Вызовите start() сначала.")

        request_kwargs: dict[str, Any] = {
            "session_id": session_id,
            "country": country.upper(),
            "query": query,
            "active_status": active_status.lower(),
            "ad_type": ad_type,
            "search_type": search_type.lower(),
        }
        if max_pages is not None:
            request_kwargs["max_pages"] = int(max_pages)
        if page_size is not None:
            request_kwargs["page_size"] = int(page_size)
        if timeout_ms is not None:
            request_kwargs["timeout_ms"] = int(timeout_ms)

        request = ad_library_pb2.SearchAdsRequest(**request_kwargs)

        # gRPC timeout = browser-agent timeout + запас на сериализацию.
        effective_timeout_ms = timeout_ms if timeout_ms is not None else 60_000
        grpc_timeout = (effective_timeout_ms / 1000.0) + 15.0

        response = await self._stub.SearchAds(request, timeout=grpc_timeout)

        raw = response.ads_json or "[]"
        try:
            parsed = json.loads(raw)
            if not isinstance(parsed, list):
                parsed = []
        except (json.JSONDecodeError, TypeError):
            parsed = []

        result = AdLibrarySearchResult(
            ad_count=int(response.ad_count),
            ads=parsed,
            duration_ms=int(response.duration_ms),
            pages_fetched=int(getattr(response, "pages_fetched", 0)),
            raw_json=raw,
        )

        if response.HasField("error") and raise_on_error:
            err = response.error
            raise AdLibraryError(
                err.message or "Ad Library вернул ошибку",
                code=int(err.code),
                type_=str(err.type),
            )

        return result

    async def search_ads_batch(
        self,
        *,
        country: str,
        queries: list[str],
        active_status: str = "active",
        ad_type: str = "all",
        search_type: str = "keyword_unordered",
        max_pages: int | None = None,
        page_size: int | None = None,
        per_query_timeout_ms: int | None = None,
        session_id: str = "",
    ) -> AdLibraryBatchResult:
        """Batch-поиск: открывает Ad Library один раз для country, прогоняет все queries
        через DOM input.fill() (как реальный юзер печатает).

        Стабильнее серии search_ads — Meta не блокирует повторные fetch.

        Returns:
            AdLibraryBatchResult со списком результатов в порядке queries.
        """
        if self._stub is None:
            raise RuntimeError("AdLibraryClient не запущен. Вызовите start() сначала.")
        if not queries:
            return AdLibraryBatchResult()

        request_kwargs: dict[str, Any] = {
            "session_id": session_id,
            "country": country.upper(),
            "queries": list(queries),
            "active_status": active_status.lower(),
            "ad_type": ad_type,
            "search_type": search_type.lower(),
        }
        if max_pages is not None:
            request_kwargs["max_pages"] = int(max_pages)
        if page_size is not None:
            request_kwargs["page_size"] = int(page_size)
        if per_query_timeout_ms is not None:
            request_kwargs["per_query_timeout_ms"] = int(per_query_timeout_ms)

        request = ad_library_pb2.SearchAdsBatchRequest(**request_kwargs)

        # gRPC timeout = per_query × количество queries + buffer на overhead
        effective_per_query_ms = (
            per_query_timeout_ms if per_query_timeout_ms is not None else 30_000
        )
        grpc_timeout = (effective_per_query_ms * len(queries) / 1000.0) + 30.0

        response = await self._stub.SearchAdsBatch(request, timeout=grpc_timeout)

        results: list[AdLibraryQueryResult] = []
        for item in response.results:
            raw = item.ads_json or "[]"
            try:
                parsed = json.loads(raw)
                if not isinstance(parsed, list):
                    parsed = []
            except (json.JSONDecodeError, TypeError):
                parsed = []

            err_type = ""
            err_msg = ""
            if item.HasField("error"):
                err_type = str(item.error.type)
                err_msg = str(item.error.message)

            results.append(
                AdLibraryQueryResult(
                    query=str(item.query),
                    ad_count=int(item.ad_count),
                    ads=parsed,
                    duration_ms=int(item.duration_ms),
                    pages_fetched=int(getattr(item, "pages_fetched", 0)),
                    error_type=err_type,
                    error_message=err_msg,
                )
            )

        return AdLibraryBatchResult(
            results=results,
            total_duration_ms=int(response.total_duration_ms),
        )
