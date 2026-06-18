# -*- coding: utf-8 -*-
"""gRPC клиент для Browser Agent сервиса.

Адаптер между Python observer worker и Node.js browser-agent.
Заменяет прямые вызовы Playwright на gRPC-вызовы.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from decimal import Decimal
from typing import AsyncIterator, TypeVar

import grpc

from clients.python_grpc.v1 import (
    browser_session_pb2,
    browser_session_pb2_grpc,
    creator_pb2,
    creator_pb2_grpc,
    scanner_pb2,
    scanner_pb2_grpc,
)
from core.browser.circuit_breaker import AsyncCircuitBreaker, CircuitOpenError

logger = logging.getLogger(__name__)
_RPC_BROWSER_CONTROL_TIMEOUT_SECONDS = 30.0
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
        await client.stop_browser()
        await client.close()
    """

    def __init__(self, config: BrowserAgentConfig):
        self.config = config
        self._channel: grpc.aio.Channel | None = None
        self._browser_stub: browser_session_pb2_grpc.BrowserSessionServiceStub | None = None
        self._scanner_stub: scanner_pb2_grpc.ScannerServiceStub | None = None
        self._creator_stub: creator_pb2_grpc.CreatorServiceStub | None = None
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
        self._creator_stub = creator_pb2_grpc.CreatorServiceStub(self._channel)
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
        """Запустить Vision профиль и подключиться к браузеру."""
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
            timeout=_RPC_BROWSER_CONTROL_TIMEOUT_SECONDS,
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

    async def disconnect_browser(self) -> None:
        """Отключиться от браузера (не останавливая Vision профиль)."""
        if not self._session_id:
            return
        await self._browser_stub.DisconnectBrowser(
            browser_session_pb2.DisconnectBrowserRequest(session_id=self._session_id),
            timeout=_RPC_BROWSER_CONTROL_TIMEOUT_SECONDS,
        )
        logger.info("Браузер отключён")

    async def stop_browser(self) -> None:
        """Полностью остановить браузер и Vision профиль."""
        if not self._session_id:
            return
        await self._browser_stub.StopBrowser(
            browser_session_pb2.StopBrowserRequest(session_id=self._session_id),
            timeout=_RPC_BROWSER_CONTROL_TIMEOUT_SECONDS,
        )
        logger.info("Браузер остановлен")
        self._session_id = None

    async def reconnect_browser(self) -> str:
        """Переподключиться к браузеру после разрыва."""
        if not self._session_id:
            return await self.ensure_browser_session()

        req = browser_session_pb2.ReconnectBrowserRequest(
            session_id=self._session_id or "",
            vision_x_token=self.config.vision_x_token,
            vision_api_url=self.config.vision_api_url,
            vision_profile_id=self.config.vision_profile_id,
        )
        try:
            resp = await self._browser_stub.ReconnectBrowser(
                req,
                timeout=_RPC_BROWSER_CONTROL_TIMEOUT_SECONDS,
            )
        except Exception as exc:
            if not _is_missing_browser_session_error(exc):
                raise
            return await self._recover_missing_browser_session(exc, "переподключения")

        self._session_id = resp.session_id
        logger.info("Браузер переподключён, session_id=%s", resp.session_id)
        return resp.session_id

    async def hard_reload(self, *, bypass_cache: bool = True) -> bool:
        """Жёсткая перезагрузка страницы Ads Manager с обходом кеша.

        Возвращает True при успехе, False при ошибке (логирует причину).
        """
        if not self._scanner_stub or not self._session_id:
            logger.warning("hard_reload: нет активной сессии browser-agent")
            return False
        req = scanner_pb2.HardReloadPageRequest(
            session_id=self._session_id,
            bypass_cache=bypass_cache,
        )
        try:
            resp = await self._scanner_stub.HardReloadPage(
                req, timeout=_RPC_BROWSER_CONTROL_TIMEOUT_SECONDS * 2
            )
        except grpc.RpcError as exc:
            logger.warning("hard_reload: gRPC error: %s", exc)
            return False
        if not resp.success:
            logger.warning("hard_reload: %s", resp.error_message)
            return False
        logger.info("hard_reload: success за %d мс", resp.reload_ms)
        return True

    async def list_campaigns(
        self, *, owner_tag: str = "", ad_account_id: str = ""
    ) -> list[dict[str, str]]:
        """Live-список кампаний по owner_tag (через Graph campaigns edge, мимо allowlist).

        Возвращает [{"id": ..., "name": ...}, ...]. При ошибке — пустой список (не
        бросает). session_id не требуется: browser-agent сам берёт активную ads-сессию
        observer'а (getPreferredSession) с кешированным graph-токеном.

        ad_account_id (L10, мульти-кабинет): числовой ID кабинета (без префикса act_) —
        browser-agent откроет/найдёт вкладку именно этого кабинета. Пусто → старое
        поведение (текущая primary-вкладка).
        """
        if not self._scanner_stub:
            logger.warning("list_campaigns: нет gRPC-канала browser-agent")
            return []
        req = scanner_pb2.ListCampaignsRequest(
            session_id=self._session_id or "",
            owner_tag=owner_tag or "",
            ad_account_id=str(ad_account_id or "").replace("act_", "").strip(),
        )
        try:
            resp = await self._scanner_stub.ListCampaigns(
                req, timeout=_RPC_BROWSER_CONTROL_TIMEOUT_SECONDS * 2
            )
        except grpc.RpcError as exc:
            logger.warning("list_campaigns: gRPC error: %s", exc)
            return []
        return [{"id": c.id, "name": c.name} for c in resp.campaigns]

    async def navigate(self, url: str, wait_until: str = "domcontentloaded") -> None:
        """Перейти на URL."""
        await self._call_with_session_recovery(
            "навигации",
            lambda: self._browser_stub.Navigate(
                browser_session_pb2.NavigateRequest(
                    session_id=self._session_id or "",
                    url=url,
                    wait_until=wait_until,
                ),
                timeout=_RPC_BROWSER_CONTROL_TIMEOUT_SECONDS,
            ),
        )

    async def run_scan_cycle(
        self,
        max_scroll_passes: int = 50,
        do_refresh: bool = True,
        reset_scroll_first: bool = True,
        settle_delay_seconds: float = 3.0,
        campaign_ids: list[str] | None = None,
        owner_tag: str | None = None,
        auto_recover_page: bool = True,
        ad_account_id: str | None = None,
    ) -> AsyncIterator[ScanProgress | ScanResult]:
        """Запустить полный цикл сканирования (am_tabular — единственный источник).

        Стримит ScanProgress для каждого прохода, в конце возвращает ScanResult.

        campaign_ids: allowlist кампаний для am-режима (#3); None → без фильтра по кампаниям.
        owner_tag: если campaign_ids пуст, am сам резолвит campaign.id по owner_tag (тянет
            только свой скоуп, а не весь кабинет). None/"" → без резолва.
        auto_recover_page: self-heal Layer 2 — если browser-agent не смог сам переоткрыть
            primary-вкладку Ads Manager (браузер/CDP мертвы) и вернул «страница недоступна»,
            один раз эскалируем reconnect_browser() и повторяем скан. Gated флагом
            vision_config.auto_restart_on_missing_cdp (прокидывает observer).
        ad_account_id: мульти-кабинет — числовой ID кабинета (без act_), который сканируем;
            browser-agent найдёт/откроет вкладку этого кабинета и сверит act из сниффа.
            None/"" → legacy одно-кабинетный путь (текущая primary-вкладка).
        """
        # Проверяем circuit-breaker до начала стриминга (включая переход OPEN → HALF_OPEN)
        try:
            await self._circuit_breaker.check_open()
        except CircuitOpenError as exc:
            raise BrowserUnavailableError(exc) from exc

        yielded_any = False
        recovered_missing_session = False
        recovered_missing_page = False

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
                ad_account_id=ad_account_id or "",
            )

            stream = None
            completed = False
            try:
                stream = self._scanner_stub.RunScanCycle(req)
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
                # Primary-вкладка недоступна и Layer 1 (browser-agent) не справился —
                # эскалируем reconnect один раз и повторяем скан (gated auto_recover_page).
                if (
                    auto_recover_page
                    and not yielded_any
                    and not recovered_missing_page
                    and _is_missing_primary_page_error(exc)
                ):
                    recovered_missing_page = True
                    logger.warning(
                        "scan: primary-страница недоступна → reconnect browser + повтор скана"
                    )
                    try:
                        await self.reconnect_browser()
                    except Exception:
                        logger.exception("scan: reconnect после page-unavailable не удался")
                        await self._circuit_breaker.record_failure(exc)
                        raise
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

    async def start_recording(self, plan_name: str) -> tuple[bool, str]:
        """Запустить запись плана через recorder в браузере."""
        resp = await self._call_with_session_recovery(
            "запуска recorder",
            lambda: self._creator_stub.StartRecording(
                creator_pb2.StartRecordingRequest(
                    session_id=self._session_id or "",
                    plan_name=plan_name,
                ),
                timeout=_RPC_BROWSER_CONTROL_TIMEOUT_SECONDS,
            ),
        )
        return resp.started, resp.message

    async def stop_recording(self) -> tuple[bool, str, int]:
        """Остановить запись и получить JSON-плана."""
        resp = await self._call_with_session_recovery(
            "остановки recorder",
            lambda: self._creator_stub.StopRecording(
                creator_pb2.StopRecordingRequest(session_id=self._session_id or ""),
                timeout=_RPC_BROWSER_CONTROL_TIMEOUT_SECONDS,
            ),
        )
        return resp.stopped, resp.plan_json, resp.recorded_steps

    async def get_recorder_status(self) -> tuple[bool, str, int]:
        """Получить текущий статус recorder."""
        resp = await self._call_with_session_recovery(
            "статуса recorder",
            lambda: self._creator_stub.GetRecorderStatus(
                creator_pb2.GetRecorderStatusRequest(session_id=self._session_id or ""),
                timeout=_RPC_BROWSER_CONTROL_TIMEOUT_SECONDS,
            ),
        )
        return resp.recording, resp.plan_name, resp.recorded_steps

    async def run_plan(
        self,
        plan_json: str,
        variables_json: str,
    ) -> AsyncIterator["creator_pb2.PlanEvent"]:
        """Запустить план через executor в браузере. Стримит PlanEvent."""
        await self.ensure_browser_session()
        req = creator_pb2.RunPlanRequest(
            session_id=self._session_id or "",
            plan_json=plan_json,
            variables_json=variables_json,
        )
        stream = self._creator_stub.RunPlan(req)
        try:
            async for event in stream:
                yield event
        finally:
            if hasattr(stream, "cancel"):
                try:
                    stream.cancel()
                except Exception:
                    pass

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
        campaign_name=proto.campaign_name,
        adset_name=proto.adset_name,
        ad_name=proto.ad_name,
        delivery_status=proto.delivery_status,
        spend=_dec(proto.spend) or Decimal("0"),
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
    )
