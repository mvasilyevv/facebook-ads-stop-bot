# -*- coding: utf-8 -*-
"""gRPC клиент для Browser Agent сервиса.

Адаптер между Python observer worker и Node.js browser-agent.
Заменяет прямые вызовы Playwright на gRPC-вызовы.
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import AsyncIterator, TypeVar

import grpc

from clients.python_grpc.v1 import (
    browser_session_pb2,
    browser_session_pb2_grpc,
    scanner_pb2,
    scanner_pb2_grpc,
)
from core.browser.circuit_breaker import AsyncCircuitBreaker, CircuitOpenError
from core.deadlines import bounded_timeout_seconds
from core.meta_api.identity import require_ad_account_id

logger = logging.getLogger(__name__)
_RPC_BROWSER_CONTROL_TIMEOUT_SECONDS = 30.0
_RPC_BROWSER_LIFECYCLE_TIMEOUT_SECONDS = 120.0
_RPC_SCAN_TIMEOUT_SECONDS = 120.0
_T = TypeVar("_T")


class BrowserUnavailableError(RuntimeError):
    """Browser-agent недоступен — circuit-breaker открыт.

    Вызывающий код может поймать это исключение вместо ожидания таймаута gRPC.
    """

    def __init__(self, cause: CircuitOpenError) -> None:
        self.cause = cause
        super().__init__(f"Browser-agent недоступен: circuit-breaker открыт. {cause}")


class ScanDataUnavailableError(RuntimeError):
    """Ошибка: данные сканирования недоступны после всех попыток восстановления."""

    def __init__(
        self,
        *,
        attempts: int,
        retry_interval_seconds: float,
        reason: str | None = None,
    ):
        self.attempts = attempts
        self.retry_interval_seconds = retry_interval_seconds
        self.reason = (reason or "Данные сканирования недоступны").strip().rstrip(".")
        super().__init__(
            f"{self.reason}. "
            f"Подряд неудачных циклов сканирования: {attempts}. "
            f"Интервал повтора: {retry_interval_seconds}с"
        )


@dataclass
class BrowserAgentConfig:
    """Конфигурация подключения к browser-agent."""

    grpc_host: str = "localhost"
    grpc_port: int = 50051
    vision_x_token: str = ""
    vision_api_url: str = "http://127.0.0.1:3030"
    vision_profile_id: str = ""
    vision_folder_id: str | None = None
    viewport_width: int = 1280
    viewport_height: int = 800


@dataclass
class ScanProgress:
    """Промежуточный результат сканирования."""

    pass_number: int
    rows_so_far: int
    at_bottom: bool
    new_rows_count: int
    new_rows: list


@dataclass
class ScanResult:
    """Полный результат сканирования."""

    rows: list  # list of ScannedAdRow (из core.scanner.models)
    total_passes: int
    duration_seconds: float
    # 0 = старый producer без fail-closed metric completeness semantics.
    metrics_contract_revision: int
    dismissed_modals: list[str] = field(default_factory=list)
    unknown_modal_artifacts: list[str] = field(default_factory=list)
    # Тайминги фаз цикла, заполненные browser-agent'ом
    phase_timings: dict[str, int] = field(default_factory=dict)
    # fb_ad_id строк, у которых какие-то обязательные колонки не дочитались
    partial_row_ids: list[str] = field(default_factory=list)
    # Коды-маркеры аномалий: "loader_visible_long", "header_missing_columns", ...
    warnings: list[str] = field(default_factory=list)
    # "no_active_ads" | "filter_excludes_all" | "table_not_found" | None
    empty_reason: str | None = None
    # Строк, у которых все критические метрики пустые (для детекции STALE_DATA)
    rows_with_all_metrics_empty: int = 0


class BrowserAgentClient:
    """Клиент для Node.js browser-agent через gRPC.

    Usage:
        client = BrowserAgentClient(config)
        await client.start()
        session_id = await client.start_browser()
        rows = await client.run_scan_cycle()
        await client.close()
    """

    def __init__(self, config: BrowserAgentConfig):
        self.config = config
        self._channel: grpc.aio.Channel | None = None
        self._browser_stub: browser_session_pb2_grpc.BrowserSessionServiceStub | None = None
        self._scanner_stub: scanner_pb2_grpc.ScannerServiceStub | None = None
        self._session_id: str | None = None
        self._cdp_port: int | None = None
        # Circuit-breaker для gRPC-вызовов к browser-agent:
        # 3 фейла подряд → OPEN на 60 сек, пробный запрос → CLOSED или OPEN снова.
        self._circuit_breaker = AsyncCircuitBreaker(
            name="browser-agent",
            failure_threshold=3,
            recovery_timeout=60.0,
        )

    async def start(self) -> None:
        """Открыть gRPC канал."""
        self._channel = grpc.aio.insecure_channel(
            f"{self.config.grpc_host}:{self.config.grpc_port}",
            options=[
                ("grpc.max_receive_message_length", 50 * 1024 * 1024),  # 50MB
                ("grpc.max_send_message_length", 50 * 1024 * 1024),
            ],
        )
        self._browser_stub = browser_session_pb2_grpc.BrowserSessionServiceStub(self._channel)
        self._scanner_stub = scanner_pb2_grpc.ScannerServiceStub(self._channel)
        logger.info("gRPC канал открыт: %s:%d", self.config.grpc_host, self.config.grpc_port)

    async def close(self) -> None:
        """Закрыть gRPC канал."""
        if self._channel:
            await self._channel.close()
            logger.info("gRPC канал закрыт")

    @property
    def session_id(self) -> str | None:
        return self._session_id

    async def ensure_browser_session(self) -> str:
        """Гарантировать наличие активной browser-agent сессии."""
        if self._session_id:
            return self._session_id

        logger.warning(
            "browser-agent session_id отсутствует, создаю новую сессию для Vision-профиля %s",
            self.config.vision_profile_id or "default",
        )
        return await self.start_browser()

    async def start_browser(self) -> str:
        """Attach a process-local session to an already-live Vision profile."""
        req = browser_session_pb2.StartBrowserRequest(
            vision_x_token=self.config.vision_x_token,
            vision_api_url=self.config.vision_api_url,
            vision_profile_id=self.config.vision_profile_id,
            vision_folder_id=self.config.vision_folder_id or "",
            viewport_width=self.config.viewport_width,
            viewport_height=self.config.viewport_height,
        )
        resp = await self._browser_stub.StartBrowser(
            req,
            timeout=bounded_timeout_seconds(_RPC_BROWSER_LIFECYCLE_TIMEOUT_SECONDS),
        )
        self._session_id = resp.session_id
        self._cdp_port = resp.profile.cdp_port
        logger.info(
            "Браузер запущен, session_id=%s, cdp_port=%d", resp.session_id, resp.profile.cdp_port
        )
        return resp.session_id

    @property
    def cdp_url(self) -> str | None:
        """HTTP-URL CDP-эндпоинта (Playwright connect_over_cdp сам резолвит ws через /json/version)."""
        if self._cdp_port is None:
            return None
        return f"http://localhost:{self._cdp_port}"

    async def reconnect_browser(self) -> str:
        """Переподключиться к браузеру после разрыва."""
        if not self._session_id:
            return await self.ensure_browser_session()

        req = browser_session_pb2.ReconnectBrowserRequest(
            session_id=self._session_id or "",
        )
        try:
            resp = await self._browser_stub.ReconnectBrowser(
                req,
                timeout=bounded_timeout_seconds(_RPC_BROWSER_LIFECYCLE_TIMEOUT_SECONDS),
            )
        except Exception as exc:
            if not _is_missing_browser_session_error(exc):
                raise
            return await self._recover_missing_browser_session(exc, "переподключения")

        self._session_id = resp.session_id
        logger.info("Браузер переподключён, session_id=%s", resp.session_id)
        return resp.session_id

    async def recover_browser_profile_under_maintenance(
        self,
        *,
        maintenance_owner: str,
    ) -> str:
        """Force-restart the canonical Vision profile under an exclusive DB lease."""
        if len(maintenance_owner) != 32 or any(
            char not in "0123456789abcdef" for char in maintenance_owner
        ):
            raise ValueError("valid browser maintenance owner is required")
        capability_secret = os.environ.get(
            "BROWSER_MAINTENANCE_CAPABILITY_SECRET",
            "",
        )
        if len(capability_secret) < 48:
            raise RuntimeError("browser maintenance capability secret is unavailable")
        capability_expires_at = int(time.time()) + 30
        capability_nonce = secrets.token_hex(16)
        vision_folder_id = self.config.vision_folder_id or ""
        token_digest = hashlib.sha256(
            self.config.vision_x_token.encode(),
        ).hexdigest()
        capability_payload = "\n".join(
            (
                "recover_browser_profile/v1",
                self.config.vision_profile_id,
                maintenance_owner,
                str(capability_expires_at),
                capability_nonce,
                self.config.vision_api_url,
                vision_folder_id,
                token_digest,
            )
        ).encode()
        capability_signature = hmac.new(
            capability_secret.encode(),
            capability_payload,
            hashlib.sha256,
        ).hexdigest()
        req = browser_session_pb2.RecoverBrowserProfileRequest(
            vision_x_token=self.config.vision_x_token,
            vision_api_url=self.config.vision_api_url,
            vision_profile_id=self.config.vision_profile_id,
            vision_folder_id=vision_folder_id,
            maintenance_owner=maintenance_owner,
            capability_expires_at=capability_expires_at,
            capability_nonce=capability_nonce,
            capability_signature=capability_signature,
        )
        resp = await self._browser_stub.RecoverBrowserProfileUnderMaintenance(
            req,
            timeout=bounded_timeout_seconds(_RPC_BROWSER_LIFECYCLE_TIMEOUT_SECONDS),
        )
        self._session_id = resp.session_id
        self._cdp_port = resp.profile.cdp_port
        logger.info(
            "Vision profile recovered under maintenance, session_id=%s",
            resp.session_id,
        )
        return resp.session_id

    async def list_campaigns(
        self, *, ad_account_id: str, owner_tag: str = ""
    ) -> list[dict[str, str]]:
        """Live-список кампаний по owner_tag (через Graph campaigns edge, мимо allowlist).

        Возвращает [{"id": ..., "name": ...}, ...]. Ошибка канала/сессии
        пробрасывается: unavailable нельзя превращать в подтверждённый пустой список.
        browser-agent использует только exact process-local session_id этого клиента;
        fallback на чужую preferred session запрещён.

        ad_account_id: обязательный числовой ID кабинета. Текущая browser-вкладка
        никогда не используется как неявная identity.
        """
        if not self._scanner_stub:
            raise RuntimeError("browser-agent channel is not initialized")
        account_id = require_ad_account_id(ad_account_id)
        req = scanner_pb2.ListCampaignsRequest(
            session_id=self._session_id or "",
            owner_tag=owner_tag or "",
            ad_account_id=account_id,
        )
        resp = await self._scanner_stub.ListCampaigns(
            req,
            timeout=bounded_timeout_seconds(_RPC_BROWSER_CONTROL_TIMEOUT_SECONDS * 2),
        )
        return [{"id": c.id, "name": c.name} for c in resp.campaigns]

    async def open_cabinet_tabs(self, ad_account_ids: list[str]) -> list[dict]:
        """Открыть вкладки Ads Manager для кабинетов (фаза подготовки перед сканом).

        Идемпотентно: уже открытую вкладку кабинета браузер-агент переиспользует.
        Возвращает per-cabinet результаты: [{ad_account_id, opened, url, error}].
        Ошибка одного кабинета не валит остальные (агрегируется в результат).
        """
        ids = [require_ad_account_id(account_id) for account_id in ad_account_ids]
        if not ids:
            return []
        # По ~20с на кабинет (page.goto), минимум 60с — открытие нескольких вкладок дольше
        # обычного контрол-вызова.
        timeout = max(60.0, 20.0 * len(ids))
        resp = await self._call_with_session_recovery(
            "открытия вкладок кабинетов",
            lambda: self._browser_stub.OpenCabinetTabs(
                browser_session_pb2.OpenCabinetTabsRequest(
                    session_id=self._session_id or "",
                    ad_account_ids=ids,
                ),
                timeout=bounded_timeout_seconds(timeout),
            ),
        )
        return [
            {
                "ad_account_id": r.ad_account_id,
                "opened": r.opened,
                "url": r.url,
                "error": r.error,
            }
            for r in resp.results
        ]

    async def run_scan_cycle(
        self,
        *,
        ad_account_id: str,
        max_scroll_passes: int = 50,
        do_refresh: bool = True,
        reset_scroll_first: bool = True,
        settle_delay_seconds: float = 3.0,
        campaign_ids: list[str] | None = None,
        owner_tag: str | None = None,
        am_columns_qs: str | None = None,
        timeout_seconds: float | None = None,
    ) -> AsyncIterator[ScanProgress | ScanResult]:
        """Запустить полный цикл сканирования (am_tabular — единственный источник).

        Стримит ScanProgress для каждого прохода, в конце возвращает ScanResult.

        campaign_ids: allowlist кампаний для am-режима (#3); None → без фильтра по кампаниям.
        owner_tag: если campaign_ids пуст, am сам резолвит campaign.id по owner_tag (тянет
            только свой скоуп, а не весь кабинет). None/"" → без резолва.
        ad_account_id: обязательный числовой ID кабинета; browser-agent открывает
            его отдельную scan-page и сверяет act из GraphContext.
        am_columns_qs: presentation-only query видимой вкладки Ads Manager; пусто
            сохраняет прежний fallback browser-agent env → встроенный default.
        """
        # Проверяем circuit-breaker до начала стриминга (включая переход OPEN → HALF_OPEN)
        try:
            await self._circuit_breaker.check_open()
        except CircuitOpenError as exc:
            raise BrowserUnavailableError(exc) from exc
        account_id = require_ad_account_id(ad_account_id)

        yielded_any = False
        recovered_missing_session = False

        while True:
            await self.ensure_browser_session()
            req = scanner_pb2.RunScanCycleRequest(
                session_id=self._session_id or "",
                max_scroll_passes=max_scroll_passes,
                do_refresh=do_refresh,
                reset_scroll_first=reset_scroll_first,
                settle_delay_seconds=settle_delay_seconds,
                campaign_ids=campaign_ids or [],
                owner_tag=owner_tag or "",
                ad_account_id=account_id,
                am_columns_qs=am_columns_qs or "",
            )

            stream = None
            completed = False
            try:
                rpc_timeout = bounded_timeout_seconds(
                    timeout_seconds if timeout_seconds is not None else _RPC_SCAN_TIMEOUT_SECONDS
                )
                stream = self._scanner_stub.RunScanCycle(req, timeout=rpc_timeout)
                async for event in stream:
                    if event.HasField("progress"):
                        p = event.progress
                        new_rows = [_proto_to_row(r) for r in p.new_rows]
                        yielded_any = True
                        yield ScanProgress(
                            pass_number=p.pass_number,
                            rows_so_far=p.rows_so_far,
                            at_bottom=p.scroll_metrics.at_bottom
                            if p.HasField("scroll_metrics")
                            else False,
                            new_rows_count=len(new_rows),
                            new_rows=new_rows,
                        )
                    elif event.HasField("complete"):
                        c = event.complete
                        rows = [_proto_to_row(r) for r in c.all_rows]
                        completed = True
                        yielded_any = True
                        yield ScanResult(
                            rows=rows,
                            total_passes=c.total_passes,
                            duration_seconds=c.duration_seconds,
                            metrics_contract_revision=c.metrics_contract_revision,
                            dismissed_modals=list(c.dismissed_modals),
                            unknown_modal_artifacts=list(c.unknown_modal_artifacts),
                            phase_timings={
                                "refresh_ms": c.phase_timings.refresh_ms,
                                "first_row_ms": c.phase_timings.first_row_ms,
                                "scroll_ms": c.phase_timings.scroll_ms,
                                "parse_ms": c.phase_timings.parse_ms,
                                "total_ms": c.phase_timings.total_ms,
                            },
                            partial_row_ids=list(c.partial_row_ids),
                            warnings=list(c.warnings),
                            empty_reason=c.empty_reason or None,
                            rows_with_all_metrics_empty=c.rows_with_all_metrics_empty,
                        )
                    elif event.HasField("error"):
                        e = event.error
                        logger.warning(
                            "Ошибка сканирования (attempt %d, recoverable=%s): %s",
                            e.attempt,
                            e.recoverable,
                            e.message,
                        )
                        raise RuntimeError(e.message or "Browser-agent вернул ошибку сканирования.")
                # Поток завершён успешно — фиксируем успех в circuit-breaker
                await self._circuit_breaker.record_success()
                return
            except Exception as exc:
                if (
                    not yielded_any
                    and not recovered_missing_session
                    and _is_missing_browser_session_error(exc)
                ):
                    recovered_missing_session = True
                    await self._recover_missing_browser_session(exc, "сканирования")
                    continue
                # Фиксируем транспортную ошибку в circuit-breaker (не сессионные/page сбои:
                # page-unavailable — это живой browser-agent без вкладки, не транспортный отказ).
                if not _is_missing_browser_session_error(
                    exc
                ) and not _is_missing_primary_page_error(exc):
                    await self._circuit_breaker.record_failure(exc)
                raise
            finally:
                if stream is not None and not completed and hasattr(stream, "cancel"):
                    stream.cancel()

    async def _call_with_session_recovery(
        self,
        operation_name: str,
        call_factory: Callable[[], Awaitable[_T]],
    ) -> _T:
        """Выполнить gRPC-вызов с восстановлением сессии и защитой circuit-breaker."""
        await self.ensure_browser_session()
        try:
            return await self._circuit_breaker.call(call_factory)
        except CircuitOpenError as exc:
            raise BrowserUnavailableError(exc) from exc
        except Exception as exc:
            if not _is_missing_browser_session_error(exc):
                raise
            await self._recover_missing_browser_session(exc, operation_name)
            # Повторный вызов тоже проходит через circuit-breaker
            try:
                return await self._circuit_breaker.call(call_factory)
            except CircuitOpenError as cb_exc:
                raise BrowserUnavailableError(cb_exc) from cb_exc

    async def _recover_missing_browser_session(self, exc: Exception, operation_name: str) -> str:
        old_session_id = self._session_id
        detail = _rpc_error_detail(exc)
        logger.warning(
            "browser-agent потерял сессию %s во время %s: %s. Создаю новую сессию.",
            old_session_id or "unknown",
            operation_name,
            detail or exc,
        )
        self._session_id = None
        return await self.start_browser()


def _is_missing_browser_session_error(exc: Exception) -> bool:
    """Проверяет, что browser-agent потерял локальную сессию после рестарта процесса.

    Сессия теряется двумя путями (после рестарта browser_agent старый session_id протух):
    1) stream error-event в RunScanCycle — browser-agent шлёт ScanError, клиент
       превращает его в RuntimeError('Сессия <id> не найдена') БЕЗ gRPC-кода.
       (Симметрично _is_missing_primary_page_error — оба распознаём по тексту.)
    2) gRPC-статус NOT_FOUND на unary-вызовах — code() == NOT_FOUND.

    Раньше требовался ТОЛЬКО код NOT_FOUND → путь (1) не распознавался, и observer
    залипал на протухшей сессии после рестарта browser_agent (монитор стоял до ручного
    рестарта observer). Теперь сначала маркеры текста, затем код как доп. триггер.
    """
    message = _rpc_error_detail(exc).casefold()
    has_session_marker = "сесс" in message or "session" in message
    has_missing_marker = "не найден" in message or "not found" in message
    if has_session_marker and has_missing_marker:
        return True

    # Фолбэк: gRPC-статус NOT_FOUND (unary) — даже если текст пустой.
    code_getter = getattr(exc, "code", None)
    if callable(code_getter):
        try:
            return code_getter() == grpc.StatusCode.NOT_FOUND
        except Exception:
            return False
    return False


def _is_missing_primary_page_error(exc: Exception) -> bool:
    """Browser-agent сообщил, что primary-вкладка Ads Manager недоступна.

    Это НЕ потеря gRPC-сессии (та — NOT_FOUND), а ошибка скан-стрима: browser-agent шлёт
    ScanError(event.error) с текстом 'Основная страница браузера недоступна', клиент
    превращает его в RuntimeError. Layer 1 (browser-agent) сам пытается переоткрыть вкладку;
    если не смог (браузер/CDP мертвы) — ошибка долетает сюда и клиент эскалирует reconnect.
    """
    message = _rpc_error_detail(exc).casefold()
    return "страница браузера недоступна" in message


def _rpc_error_detail(exc: Exception) -> str:
    """Возвращает человекочитаемый текст gRPC-ошибки."""
    details_getter = getattr(exc, "details", None)
    if callable(details_getter):
        try:
            details = details_getter()
            if details:
                return str(details)
        except Exception:
            pass
    return str(exc)


def _proto_to_row(proto) -> object:
    """Конвертировать protobuf ScannedAdRow в Python dataclass."""
    from core.scanner.models import ScannedAdRow

    def _dec(val: str) -> Decimal | None:
        if not val or val == "":
            return None
        try:
            return Decimal(val)
        except Exception:
            return None

    return ScannedAdRow(
        fb_ad_id=proto.fb_ad_id,
        campaign_id=proto.campaign_id,
        adset_id=proto.adset_id,
        campaign_name=proto.campaign_name,
        adset_name=proto.adset_name,
        ad_name=proto.ad_name,
        delivery_status=proto.delivery_status,
        spend=_dec(proto.spend) or Decimal("0"),
        moderation_reason=(
            proto.moderation_reason if proto.HasField("moderation_reason") else None
        ),
        budget=proto.budget,
        reach=proto.reach,
        impressions=proto.impressions,
        clicks=proto.clicks,
        cpc=_dec(proto.cpc),
        ctr=_dec(proto.ctr),
        outbound_clicks=proto.outbound_clicks,
        outbound_ctr=_dec(proto.outbound_ctr),
        landing_page_views=proto.landing_page_views,
        cost_per_landing_page_view=_dec(proto.cost_per_landing_page_view),
        cost_per_result=_dec(proto.cost_per_result),
        cpm=_dec(proto.cpm),
        frequency=_dec(proto.frequency),
        leads=proto.leads,
        cost_per_lead=_dec(proto.cost_per_lead),
        registrations=proto.registrations,
        cost_per_registration=_dec(proto.cost_per_registration),
        deposits=proto.deposits,
        resolved_offer_code=proto.resolved_offer_code or None,
        creative_thumb_url=proto.creative_thumb_url,
        creative_image_url=proto.creative_image_url,
        adset_pixel_id=proto.adset_pixel_id,
        adset_daily_budget=proto.adset_daily_budget,
        adset_lifetime_budget=proto.adset_lifetime_budget,
        adset_budget_remaining=proto.adset_budget_remaining,
        adset_learning_stage=proto.adset_learning_stage,
    )
