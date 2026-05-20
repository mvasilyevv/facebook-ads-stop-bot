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

logger = logging.getLogger(__name__)
_RPC_BROWSER_CONTROL_TIMEOUT_SECONDS = 30.0
_RPC_TOGGLE_FIND_TIMEOUT_SECONDS = 45.0
_RPC_TOGGLE_READ_TIMEOUT_SECONDS = 12.0
_RPC_TOGGLE_CLICK_TIMEOUT_SECONDS = 15.0
_RPC_TOGGLE_CONFIRM_EXTRA_TIMEOUT_SECONDS = 15.0
_T = TypeVar("_T")


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
    ) -> AsyncIterator[ScanProgress | ScanResult]:
        """Запустить полный цикл сканирования.

        Стримит ScanProgress для каждого прохода, в конце возвращает ScanResult.
        """
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
                raise
            finally:
                if stream is not None and not completed and hasattr(stream, "cancel"):
                    stream.cancel()

    async def refresh_table(self) -> bool:
        """Нажать кнопку «Refresh» в Ads Manager."""
        resp = await self._call_with_session_recovery(
            "обновления таблицы",
            lambda: self._scanner_stub.RefreshTable(
                scanner_pb2.RefreshTableRequest(session_id=self._session_id or "")
            ),
        )
        return resp.refreshed

    async def scroll_and_parse(
        self,
        scroll_amount: int = 320,
        wait_for_stable: bool = True,
    ) -> tuple[list, dict]:
        """Прокрутить таблицу вниз и вернуть видимые строки с метриками скролла."""
        resp = await self._call_with_session_recovery(
            "скролла и парсинга таблицы",
            lambda: self._scanner_stub.ScrollAndParse(
                scanner_pb2.ScrollAndParseRequest(
                    session_id=self._session_id or "",
                    scroll_amount=scroll_amount,
                    wait_for_stable=wait_for_stable,
                    stable_timeout_seconds=2.0,
                )
            ),
        )
        return (
            [_proto_to_row(row) for row in resp.new_rows],
            {
                "found": resp.scroll_metrics.found,
                "scroll_top": resp.scroll_metrics.scroll_top,
                "max_scroll_top": resp.scroll_metrics.max_scroll_top,
                "at_bottom": resp.scroll_metrics.at_bottom,
            },
        )

    async def get_scroll_metrics(self) -> dict:
        """Получить метрики скролла таблицы."""
        resp = await self._call_with_session_recovery(
            "чтения метрик скролла",
            lambda: self._scanner_stub.GetScrollMetrics(
                scanner_pb2.GetScrollMetricsRequest(session_id=self._session_id or "")
            ),
        )
        return {
            "found": resp.metrics.found,
            "scroll_top": resp.metrics.scroll_top,
            "max_scroll_top": resp.metrics.max_scroll_top,
            "at_bottom": resp.metrics.at_bottom,
        }

    async def reset_scroll(self) -> int:
        """Сбросить скролл таблицы наверх."""
        resp = await self._call_with_session_recovery(
            "сброса скролла",
            lambda: self._scanner_stub.ResetScroll(
                scanner_pb2.ResetScrollRequest(session_id=self._session_id or "")
            ),
        )
        return resp.containers_reset

    async def get_visible_row_ids(self) -> list[str]:
        """Получить ID видимых строк."""
        resp = await self._call_with_session_recovery(
            "чтения видимых строк",
            lambda: self._scanner_stub.GetVisibleRowIds(
                scanner_pb2.GetVisibleRowIdsRequest(session_id=self._session_id or "")
            ),
        )
        return list(resp.row_ids)

    async def find_toggle_cell(
        self,
        fb_ad_id: str,
        reset_to_top: bool = True,
        max_scroll_passes: int | None = None,
    ) -> dict:
        """Найти toggle-ячейку для объявления."""
        resp = await self._call_with_session_recovery(
            "поиска toggle",
            lambda: self._scanner_stub.FindToggleCell(
                scanner_pb2.FindToggleCellRequest(
                    session_id=self._session_id or "",
                    fb_ad_id=fb_ad_id,
                    reset_to_top=reset_to_top,
                    max_scroll_passes=max_scroll_passes or 0,
                ),
                timeout=_RPC_TOGGLE_FIND_TIMEOUT_SECONDS,
            ),
        )
        return {
            "found": resp.found,
            "cell_x": resp.cell_x,
            "cell_y": resp.cell_y,
            "aria_checked": resp.aria_checked,
        }

    async def read_toggle_state(self, fb_ad_id: str) -> str:
        """Прочитать aria-checked toggle."""
        resp = await self._call_with_session_recovery(
            "чтения toggle",
            lambda: self._scanner_stub.ReadToggleState(
                scanner_pb2.ReadToggleStateRequest(
                    session_id=self._session_id or "",
                    fb_ad_id=fb_ad_id,
                ),
                timeout=_RPC_TOGGLE_READ_TIMEOUT_SECONDS,
            ),
        )
        return resp.aria_checked

    async def toggle_ad(self, fb_ad_id: str, target_state: bool = True) -> dict:
        """Переключить on/off switch объявления.

        Args:
            fb_ad_id: ID объявления.
            target_state: True = включить (ON), False = отключить (OFF).
        """
        resp = await self._call_with_session_recovery(
            "клика по toggle",
            lambda: self._scanner_stub.ToggleAd(
                scanner_pb2.ToggleAdRequest(
                    session_id=self._session_id or "",
                    fb_ad_id=fb_ad_id,
                    target_state=target_state,
                ),
                timeout=_RPC_TOGGLE_CLICK_TIMEOUT_SECONDS,
            ),
        )
        return {
            "success": resp.success,
            "final_state": resp.final_state,
        }

    async def wait_for_toggle_confirmation(
        self,
        fb_ad_id: str,
        expected_checked: str = "false",
        required_reads: int = 2,
        poll_delays_seconds: list[float] | None = None,
        max_scroll_passes_restore: int = 30,
    ) -> dict:
        """Ждать подтверждения toggle через повторные чтения aria-checked.

        Args:
            fb_ad_id: ID объявления.
            expected_checked: "true" для enable, "false" для disable.
            required_reads: Сколько раз подряд нужно прочитать expected_checked.
            poll_delays_seconds: Задержки между попытками (секунды).
            max_scroll_passes_restore: Макс. проходов скролла для restore visibility.

        Returns:
            dict с полями success, message, final_aria_checked, reads_matched.
        """
        delays = poll_delays_seconds or [0.0, 3.0, 3.0, 3.0, 3.0, 4.0, 4.0, 5.0, 5.0]
        rpc_timeout = sum(delays) + _RPC_TOGGLE_CONFIRM_EXTRA_TIMEOUT_SECONDS
        resp = await self._call_with_session_recovery(
            "подтверждения toggle",
            lambda: self._scanner_stub.WaitForToggleConfirmation(
                scanner_pb2.WaitForToggleConfirmationRequest(
                    session_id=self._session_id or "",
                    fb_ad_id=fb_ad_id,
                    expected_checked=expected_checked,
                    required_reads=required_reads,
                    poll_delays_seconds=delays,
                    max_scroll_passes_restore=max_scroll_passes_restore,
                ),
                timeout=rpc_timeout,
            ),
        )
        return {
            "success": resp.success,
            "message": resp.message,
            "final_aria_checked": resp.final_aria_checked,
            "reads_matched": resp.reads_matched,
        }

    async def validate_columns(self) -> dict:
        """Проверить наличие всех необходимых колонок в таблице Ads Manager.

        Returns:
            dict с полями valid, missing_columns, found_columns, error_message.
        """
        resp = await self._call_with_session_recovery(
            "валидации колонок",
            lambda: self._scanner_stub.ValidateColumns(
                scanner_pb2.ValidateColumnsRequest(session_id=self._session_id or "")
            ),
        )
        return {
            "valid": resp.valid,
            "missing_columns": list(resp.missing_columns),
            "found_columns": list(resp.found_columns),
            "error_message": resp.error_message,
        }

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
        await self.ensure_browser_session()
        try:
            return await call_factory()
        except Exception as exc:
            if not _is_missing_browser_session_error(exc):
                raise
            await self._recover_missing_browser_session(exc, operation_name)
            return await call_factory()

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
    """Проверяет, что browser-agent потерял локальную сессию после рестарта процесса."""
    code = None
    code_getter = getattr(exc, "code", None)
    if callable(code_getter):
        try:
            code = code_getter()
        except Exception:
            code = None

    if code != grpc.StatusCode.NOT_FOUND:
        return False

    message = _rpc_error_detail(exc).casefold()
    has_session_marker = "сесс" in message or "session" in message
    has_missing_marker = "не найден" in message or "not found" in message
    return has_session_marker and has_missing_marker


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
