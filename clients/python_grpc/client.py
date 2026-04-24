# -*- coding: utf-8 -*-
"""gRPC клиент для Browser Agent сервиса.

Адаптер между Python observer worker и Node.js browser-agent.
Заменяет прямые вызовы Playwright на gRPC-вызовы.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal
from typing import AsyncIterator

import grpc

from clients.python_grpc.v1 import (
    browser_session_pb2,
    browser_session_pb2_grpc,
    scanner_pb2,
    scanner_pb2_grpc,
)

logger = logging.getLogger(__name__)


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


@dataclass
class ScanResult:
    """Полный результат сканирования."""

    rows: list  # list of ScannedAdRow (из core.scanner.models)
    total_passes: int
    duration_seconds: float


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
        self._session_id: str | None = None

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
        resp = await self._browser_stub.StartBrowser(req)
        self._session_id = resp.session_id
        logger.info(
            "Браузер запущен, session_id=%s, cdp_port=%d", resp.session_id, resp.profile.cdp_port
        )
        return resp.session_id

    async def disconnect_browser(self) -> None:
        """Отключиться от браузера (не останавливая Vision профиль)."""
        if not self._session_id:
            return
        await self._browser_stub.DisconnectBrowser(
            browser_session_pb2.DisconnectBrowserRequest(session_id=self._session_id)
        )
        logger.info("Браузер отключён")

    async def stop_browser(self) -> None:
        """Полностью остановить браузер и Vision профиль."""
        if not self._session_id:
            return
        await self._browser_stub.StopBrowser(
            browser_session_pb2.StopBrowserRequest(session_id=self._session_id)
        )
        logger.info("Браузер остановлен")
        self._session_id = None

    async def reconnect_browser(self) -> str:
        """Переподключиться к браузеру после разрыва."""
        req = browser_session_pb2.ReconnectBrowserRequest(
            session_id=self._session_id or "",
            vision_x_token=self.config.vision_x_token,
            vision_api_url=self.config.vision_api_url,
            vision_profile_id=self.config.vision_profile_id,
        )
        resp = await self._browser_stub.ReconnectBrowser(req)
        self._session_id = resp.session_id
        logger.info("Браузер переподключён, session_id=%s", resp.session_id)
        return resp.session_id

    async def navigate(self, url: str, wait_until: str = "domcontentloaded") -> None:
        """Перейти на URL."""
        await self._scanner_stub.Navigate(
            browser_session_pb2.NavigateRequest(
                session_id=self._session_id or "",
                url=url,
                wait_until=wait_until,
            )
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
        req = scanner_pb2.RunScanCycleRequest(
            session_id=self._session_id or "",
            max_scroll_passes=max_scroll_passes,
            do_refresh=do_refresh,
            reset_scroll_first=reset_scroll_first,
            settle_delay_seconds=settle_delay_seconds,
        )

        async for event in self._scanner_stub.RunScanCycle(req):
            if event.HasField("progress"):
                p = event.progress
                yield ScanProgress(
                    pass_number=p.pass_number,
                    rows_so_far=p.rows_so_far,
                    at_bottom=p.scroll_metrics.at_bottom if p.HasField("scroll_metrics") else False,
                    new_rows_count=len(p.new_rows),
                )
            elif event.HasField("complete"):
                c = event.complete
                rows = [_proto_to_row(r) for r in c.all_rows]
                yield ScanResult(
                    rows=rows,
                    total_passes=c.total_passes,
                    duration_seconds=c.duration_seconds,
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

    async def refresh_table(self) -> bool:
        """Нажать кнопку «Refresh» в Ads Manager."""
        resp = await self._scanner_stub.RefreshTable(
            scanner_pb2.RefreshTableRequest(session_id=self._session_id or "")
        )
        return resp.refreshed

    async def scroll_and_parse(
        self,
        scroll_amount: int = 320,
        wait_for_stable: bool = True,
    ) -> tuple[list, dict]:
        """Прокрутить таблицу вниз и вернуть видимые строки с метриками скролла."""
        resp = await self._scanner_stub.ScrollAndParse(
            scanner_pb2.ScrollAndParseRequest(
                session_id=self._session_id or "",
                scroll_amount=scroll_amount,
                wait_for_stable=wait_for_stable,
                stable_timeout_seconds=2.0,
            )
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
        resp = await self._scanner_stub.GetScrollMetrics(
            scanner_pb2.GetScrollMetricsRequest(session_id=self._session_id or "")
        )
        return {
            "found": resp.metrics.found,
            "scroll_top": resp.metrics.scroll_top,
            "max_scroll_top": resp.metrics.max_scroll_top,
            "at_bottom": resp.metrics.at_bottom,
        }

    async def reset_scroll(self) -> int:
        """Сбросить скролл таблицы наверх."""
        resp = await self._scanner_stub.ResetScroll(
            scanner_pb2.ResetScrollRequest(session_id=self._session_id or "")
        )
        return resp.containers_reset

    async def get_visible_row_ids(self) -> list[str]:
        """Получить ID видимых строк."""
        resp = await self._scanner_stub.GetVisibleRowIds(
            scanner_pb2.GetVisibleRowIdsRequest(session_id=self._session_id or "")
        )
        return list(resp.row_ids)

    async def find_toggle_cell(self, fb_ad_id: str, reset_to_top: bool = True) -> dict:
        """Найти toggle-ячейку для объявления."""
        resp = await self._scanner_stub.FindToggleCell(
            scanner_pb2.FindToggleCellRequest(
                session_id=self._session_id or "",
                fb_ad_id=fb_ad_id,
                reset_to_top=reset_to_top,
            )
        )
        return {
            "found": resp.found,
            "cell_x": resp.cell_x,
            "cell_y": resp.cell_y,
            "aria_checked": resp.aria_checked,
        }

    async def read_toggle_state(self, fb_ad_id: str) -> str:
        """Прочитать aria-checked toggle."""
        resp = await self._scanner_stub.ReadToggleState(
            scanner_pb2.ReadToggleStateRequest(
                session_id=self._session_id or "",
                fb_ad_id=fb_ad_id,
            )
        )
        return resp.aria_checked

    async def toggle_ad(self, fb_ad_id: str, target_state: bool = True) -> dict:
        """Переключить on/off switch объявления.

        Args:
            fb_ad_id: ID объявления.
            target_state: True = включить (ON), False = отключить (OFF).
        """
        resp = await self._scanner_stub.ToggleAd(
            scanner_pb2.ToggleAdRequest(
                session_id=self._session_id or "",
                fb_ad_id=fb_ad_id,
                target_state=target_state,
            )
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
        resp = await self._scanner_stub.WaitForToggleConfirmation(
            scanner_pb2.WaitForToggleConfirmationRequest(
                session_id=self._session_id or "",
                fb_ad_id=fb_ad_id,
                expected_checked=expected_checked,
                required_reads=required_reads,
                poll_delays_seconds=delays,
                max_scroll_passes_restore=max_scroll_passes_restore,
            )
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
        resp = await self._scanner_stub.ValidateColumns(
            scanner_pb2.ValidateColumnsRequest(session_id=self._session_id or "")
        )
        return {
            "valid": resp.valid,
            "missing_columns": list(resp.missing_columns),
            "found_columns": list(resp.found_columns),
            "error_message": resp.error_message,
        }


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
