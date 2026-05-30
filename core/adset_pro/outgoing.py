# -*- coding: utf-8 -*-
"""Исходящий postback — форвард конверсии во внешнюю систему/трекер.

См. META_INTEGRATION_PLAN.md §5 Волна 4 / Этап 6. Формат повторяет поля конверсии
(см. core.adset_pro.schemas.PostbackEvent / core.models.trackers.postback.TrackerPostback):
click_id, event_type(goal), revenue(payout), currency, fb_ad_id, country.

Дизайн:
- URL-шаблон с макросами (см. _MACROS). Значения URL-кодируются (urllib.parse.quote).
- OutgoingPostbackSender — httpx async + tenacity retry на 5xx/сеть. send() НИКОГДА
  не бросает наружу (возвращает OutgoingResult) — форвард не должен ронять основной flow.
- dispatch() — fire-and-forget: планирует send() в фоне и возвращает Task сразу
  (не блокирует основной flow). drain() — дождаться фоновых задач (shutdown/тесты).

Durability: для гарантии доставки при крэше форвард можно завести через task_queue
(outbox) отдельным task_type — здесь намеренно лёгкий sender без новой очереди, т.к.
адресат форварда (URL) пока конфигурируется, а не задан жёстко. См. отчёт BL-8.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from decimal import Decimal
from types import TracebackType
from typing import Any
from urllib.parse import quote

import httpx
from tenacity import (
    AsyncRetrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from core.adset_pro.errors import TemporaryError

logger = logging.getLogger(__name__)

_DEFAULT_TIMEOUT_SECONDS = 10.0


@dataclass(slots=True, frozen=True)
class OutgoingPostback:
    """Конверсия для форварда во внешнюю систему."""

    click_id: str
    event_type: str
    revenue: Decimal | None = None
    currency: str = "USD"
    fb_ad_id: str | None = None
    country: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


@dataclass(slots=True, frozen=True)
class OutgoingResult:
    """Итог одной отправки. ok=False+skipped=True — отправка отключена/нет URL."""

    ok: bool
    url: str
    status_code: int | None = None
    attempts: int = 0
    skipped: bool = False
    error: str | None = None


def _macro_values(postback: OutgoingPostback) -> dict[str, str]:
    """Готовые (ещё НЕ закодированные) значения макросов из конверсии."""
    revenue = "" if postback.revenue is None else str(postback.revenue)
    return {
        "click_id": postback.click_id,
        "event_type": postback.event_type,
        "goal": postback.event_type,  # alias под affiliate-конвенцию
        "revenue": revenue,
        "payout": revenue,  # alias
        "currency": postback.currency or "",
        "fb_ad_id": postback.fb_ad_id or "",
        "country": postback.country or "",
    }


# Поддерживаемые макросы шаблона.
_MACROS: tuple[str, ...] = (
    "click_id",
    "event_type",
    "goal",
    "revenue",
    "payout",
    "currency",
    "fb_ad_id",
    "country",
)


def build_postback_url(url_template: str, postback: OutgoingPostback) -> str:
    """Подставить макросы в URL-шаблон, URL-кодируя значения.

    Неизвестные `{...}` в шаблоне остаются как есть (не падаем). Каждое значение
    проходит quote(safe="") — безопасно для query-string.
    """
    values = _macro_values(postback)
    result = url_template
    for macro in _MACROS:
        token = "{" + macro + "}"
        if token in result:
            result = result.replace(token, quote(values[macro], safe=""))
    return result


class OutgoingPostbackSender:
    """Отправщик исходящих postback'ов с retry. Не блокирует основной flow.

    Usage:
        sender = OutgoingPostbackSender(url_template=..., enabled=True)
        await sender.start()
        sender.dispatch(postback)          # fire-and-forget, не блокирует
        results = await sender.drain()     # дождаться фоновых (shutdown/тест)
        await sender.close()
    """

    def __init__(
        self,
        *,
        url_template: str,
        enabled: bool = True,
        method: str = "GET",
        timeout_seconds: float = _DEFAULT_TIMEOUT_SECONDS,
        http_client: httpx.AsyncClient | None = None,
        max_retries: int = 3,
    ) -> None:
        self._url_template = url_template or ""
        self._enabled = enabled
        self._method = (method or "GET").upper()
        self._timeout_seconds = timeout_seconds
        self._external_client = http_client is not None
        self._http: httpx.AsyncClient | None = http_client
        self._max_retries = max(1, max_retries)
        self._bg_tasks: set[asyncio.Task[OutgoingResult]] = set()

    @classmethod
    def from_settings(cls, settings: Any, **overrides: Any) -> OutgoingPostbackSender:
        """Собрать sender из core.config.Settings."""
        params: dict[str, Any] = {
            "url_template": settings.tracker_outgoing_postback_url,
            "enabled": settings.tracker_outgoing_enabled,
            "method": settings.tracker_outgoing_method,
            "timeout_seconds": settings.tracker_outgoing_timeout_seconds,
        }
        params.update(overrides)
        return cls(**params)

    # ====================== lifecycle ======================

    async def start(self) -> None:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=self._timeout_seconds)

    async def close(self) -> None:
        await self.drain()
        if self._http is not None and not self._external_client:
            await self._http.aclose()
        self._http = None

    async def __aenter__(self) -> OutgoingPostbackSender:
        await self.start()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        await self.close()

    # ====================== send ======================

    async def send(self, postback: OutgoingPostback) -> OutgoingResult:
        """Отправить один postback с retry. НИКОГДА не бросает — возвращает результат."""
        if not self._enabled or not self._url_template:
            logger.debug("outgoing postback отключён/без URL — skip click_id=%s", postback.click_id)
            return OutgoingResult(ok=False, url="", skipped=True)

        if self._http is None:
            raise RuntimeError("OutgoingPostbackSender не запущен: вызови await start()")

        url = build_postback_url(self._url_template, postback)
        attempts = 0

        try:
            async for attempt in AsyncRetrying(
                stop=stop_after_attempt(self._max_retries),
                wait=wait_exponential(multiplier=0.5, min=0.5, max=4.0),
                retry=retry_if_exception_type((TemporaryError, httpx.TransportError)),
                reraise=True,
            ):
                with attempt:
                    attempts += 1
                    resp = await self._http.request(self._method, url)
                    if 500 <= resp.status_code < 600:
                        # 5xx — временная, провоцируем retry.
                        raise TemporaryError(
                            f"outgoing postback {resp.status_code}",
                            status_code=resp.status_code,
                        )
                    ok = 200 <= resp.status_code < 300
                    if not ok:
                        logger.warning(
                            "outgoing postback click_id=%s → HTTP %s (permanent, без retry)",
                            postback.click_id,
                            resp.status_code,
                        )
                    return OutgoingResult(
                        ok=ok, url=url, status_code=resp.status_code, attempts=attempts
                    )
        except TemporaryError as exc:
            logger.warning(
                "outgoing postback click_id=%s исчерпал retry: %s", postback.click_id, exc
            )
            return OutgoingResult(
                ok=False, url=url, status_code=exc.status_code, attempts=attempts, error=str(exc)
            )
        except httpx.TransportError as exc:
            logger.warning("outgoing postback click_id=%s сетевой сбой: %s", postback.click_id, exc)
            return OutgoingResult(ok=False, url=url, attempts=attempts, error=str(exc))
        except Exception as exc:  # noqa: BLE001 — форвард не должен ронять основной flow
            logger.exception("outgoing postback click_id=%s неожиданная ошибка", postback.click_id)
            return OutgoingResult(ok=False, url=url, attempts=attempts, error=str(exc))

        # Недостижимо (reraise=True гарантирует выход через return или except).
        return OutgoingResult(ok=False, url=url, attempts=attempts, error="retry loop exhausted")

    # ====================== non-blocking dispatch ======================

    def dispatch(self, postback: OutgoingPostback) -> asyncio.Task[OutgoingResult]:
        """Отправить в фоне (не блокирует). Возвращает Task — можно дождаться при желании."""
        task: asyncio.Task[OutgoingResult] = asyncio.create_task(self.send(postback))
        # Держим ссылку, чтобы GC не прибил фоновую задачу до завершения.
        self._bg_tasks.add(task)
        task.add_done_callback(self._bg_tasks.discard)
        return task

    async def drain(self) -> list[OutgoingResult]:
        """Дождаться всех фоновых dispatch'ей (для graceful shutdown / тестов)."""
        if not self._bg_tasks:
            return []
        results = await asyncio.gather(*list(self._bg_tasks), return_exceptions=True)
        return [r for r in results if isinstance(r, OutgoingResult)]
