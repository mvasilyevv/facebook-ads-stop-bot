# -*- coding: utf-8 -*-
"""Восстановление скана при временной недоступности данных в Ads Manager."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable

from core.scanner.models import ScannedAdRow

logger = logging.getLogger(__name__)

DEFAULT_SCAN_RECOVERY_ATTEMPTS = 5
DEFAULT_SCAN_RECOVERY_INTERVAL_SECONDS = 60.0

AvailabilityCheck = Callable[[list[ScannedAdRow]], bool]
RecoveryAttemptCallback = Callable[[int, int], Awaitable[None]]
RefreshTableCallback = Callable[[object], Awaitable[bool]]
ResetScrollCallback = Callable[[object], Awaitable[None]]
ScrollAndParseCallback = Callable[[object, object], Awaitable[list[ScannedAdRow]]]
SleepCallback = Callable[[float], Awaitable[None]]


class ScanDataUnavailableError(RuntimeError):
    """Данные таблицы не появились даже после серии page reload."""

    def __init__(self, *, attempts: int, retry_interval_seconds: float) -> None:
        self.attempts = attempts
        self.retry_interval_seconds = retry_interval_seconds
        super().__init__(
            "Данные Ads Manager не появились после "
            f"{attempts} попыток перезагрузки страницы с интервалом "
            f"{int(retry_interval_seconds)} сек. Сканирование автоматически выключено."
        )


def has_recoverable_scan_data(rows: list[ScannedAdRow]) -> bool:
    """Считает скан валидным, если парсер вернул хотя бы одну строку объявления."""
    return bool(rows)


async def _scan_once(
    *,
    page,
    parse_fn,
    refresh_table_fn: RefreshTableCallback,
    reset_scroll_fn: ResetScrollCallback,
    scroll_and_parse_fn: ScrollAndParseCallback,
    sleep_fn: SleepCallback,
    settle_delay_seconds: float,
) -> list[ScannedAdRow]:
    """Выполняет один обычный цикл скана страницы."""
    await reset_scroll_fn(page)

    logger.info("Observer: обновление таблицы")
    refreshed = await refresh_table_fn(page)
    if not refreshed:
        logger.warning("Observer: кнопка обновления не найдена, выполняю перезагрузку страницы")
        await page.reload(wait_until="domcontentloaded")

    if settle_delay_seconds > 0:
        await sleep_fn(settle_delay_seconds)

    return await scroll_and_parse_fn(page, parse_fn)


async def scan_ads_with_page_recovery(
    *,
    page,
    parse_fn,
    refresh_table_fn: RefreshTableCallback,
    reset_scroll_fn: ResetScrollCallback,
    scroll_and_parse_fn: ScrollAndParseCallback,
    sleep_fn: SleepCallback = asyncio.sleep,
    settle_delay_seconds: float = 0.0,
    max_recovery_attempts: int = DEFAULT_SCAN_RECOVERY_ATTEMPTS,
    retry_interval_seconds: float = DEFAULT_SCAN_RECOVERY_INTERVAL_SECONDS,
    availability_check: AvailabilityCheck = has_recoverable_scan_data,
    on_recovery_attempt: RecoveryAttemptCallback | None = None,
) -> list[ScannedAdRow]:
    """Пробует обычный scan, а при пустом результате восстанавливается через page reload."""
    rows = await _scan_once(
        page=page,
        parse_fn=parse_fn,
        refresh_table_fn=refresh_table_fn,
        reset_scroll_fn=reset_scroll_fn,
        scroll_and_parse_fn=scroll_and_parse_fn,
        sleep_fn=sleep_fn,
        settle_delay_seconds=settle_delay_seconds,
    )
    if availability_check(rows):
        return rows

    logger.warning(
        "Observer: парсер не получил строки объявлений, запускаю восстановление страницы"
    )

    for attempt in range(1, max_recovery_attempts + 1):
        if on_recovery_attempt is not None:
            await on_recovery_attempt(attempt, max_recovery_attempts)

        logger.warning(
            "Observer: попытка восстановления данных %s/%s — перезагружаю страницу и повторяю скан",
            attempt,
            max_recovery_attempts,
        )
        await page.reload(wait_until="domcontentloaded")

        if settle_delay_seconds > 0:
            await sleep_fn(settle_delay_seconds)

        await reset_scroll_fn(page)
        rows = await scroll_and_parse_fn(page, parse_fn)
        if availability_check(rows):
            logger.info(
                "Observer: данные после page reload восстановлены на попытке %s/%s",
                attempt,
                max_recovery_attempts,
            )
            return rows

        if attempt < max_recovery_attempts:
            logger.warning(
                "Observer: данные всё ещё недоступны после попытки %s/%s, следующая попытка через %.0f сек",
                attempt,
                max_recovery_attempts,
                retry_interval_seconds,
            )
            await sleep_fn(retry_interval_seconds)

    raise ScanDataUnavailableError(
        attempts=max_recovery_attempts,
        retry_interval_seconds=retry_interval_seconds,
    )
