# -*- coding: utf-8 -*-
"""Self-healing эскалатор для observer worker.

Отвечает за:
- Подсчёт последовательных провалов цикла.
- Мягкий reconnect при 2-м провале.
- Жёсткое stop+start при 3-м провале.
- Критический TG-алерт при 4-м и далее (раз в 30 минут).
- Алерт о неизвестных модальных окнах (не считается провалом).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import UTC, datetime

logger = logging.getLogger(__name__)

# Интервал между повторными крит-алертами, пока проблема не ушла
_CRITICAL_ALERT_COOLDOWN_SECONDS = 30 * 60  # 30 минут

# Backoff между провалами цикла (секунды): экспоненциальный, с верхним пределом.
# Без этого observer уходит в тугой цикл и за минуту накручивает тысячи провалов,
# спамит логи и не даёт реального шанса инфраструктуре восстановиться.
_FAILURE_BACKOFF_SCHEDULE_SECONDS: tuple[float, ...] = (
    5.0,  # после 1-го провала
    10.0,  # после 2-го (после reconnect)
    20.0,  # после 3-го (после жёсткого stop+start)
    60.0,  # после 4-го (крит-алерт уже ушёл)
)
_FAILURE_BACKOFF_MAX_SECONDS = 60.0


async def _load_ops_thread_id() -> int | None:
    """Всегда возвращает None — forum-topic режим удалён."""
    return None


class SelfHealingEscalator:
    """Эскалатор восстановления observer worker.

    Инкрементирует ``consecutive_failure_count`` при каждом провале цикла
    (gRPC-ошибка, таймаут, ScanDataUnavailable, сбой валидации колонок).
    Сбрасывает счётчик при успешном полном цикле.

    Логика эскалации:
        count == 1 → только WARNING-лог.
        count == 2 → мягкий reconnect_browser().
        count == 3 → жёсткий stop_browser() + sleep(2s) + start_browser().
        count >= 4 → TG-крит-алерт (один раз, затем раз в 30 мин).
    """

    def __init__(self) -> None:
        self.consecutive_failure_count: int = 0
        self._last_critical_alert_at: datetime | None = None
        # Дедуп для алертов о неизвестных модалках — не чаще раза в 30 минут
        self._last_modal_alert_at: datetime | None = None

    def record_success(self) -> None:
        """Сбросить счётчик провалов при успешном цикле."""
        if self.consecutive_failure_count > 0:
            logger.info(
                "Observer: успешный цикл — сброс счётчика провалов (было %d)",
                self.consecutive_failure_count,
            )
        self.consecutive_failure_count = 0

    async def record_failure(
        self,
        *,
        grpc_client,
        tg_client,
        tg_chat_id: str,
    ) -> None:
        """Инкрементировать счётчик и выполнить соответствующую эскалацию.

        Args:
            grpc_client: BrowserAgentClient для reconnect/stop/start.
            tg_client: TelegramBotClient для крит-алертов (может быть None).
            tg_chat_id: ID чата для крит-алертов.
        """
        self.consecutive_failure_count += 1
        count = self.consecutive_failure_count

        if count == 1:
            logger.warning("Observer: 1-й подряд провал цикла, продолжаю")

        elif count == 2:
            logger.warning("Observer: 2-й подряд провал цикла — выполняю мягкий reconnect")
            try:
                await grpc_client.reconnect_browser()
                logger.info("Observer: мягкий reconnect выполнен")
            except Exception:
                logger.warning("Observer: мягкий reconnect не удался", exc_info=True)

        elif count == 3:
            logger.warning(
                "Observer: 3-й подряд провал цикла — жёсткое восстановление (stop + start)"
            )
            try:
                await grpc_client.stop_browser()
                logger.info("Observer: браузер остановлен при жёстком восстановлении")
            except Exception:
                logger.warning(
                    "Observer: stop_browser не удался при жёстком восстановлении", exc_info=True
                )

            await asyncio.sleep(2.0)

            try:
                await grpc_client.start_browser()
                logger.info("Observer: жёсткое восстановление завершено — браузер перезапущен")
            except Exception:
                logger.warning(
                    "Observer: start_browser не удался при жёстком восстановлении", exc_info=True
                )

        elif count >= 4:
            await self._send_critical_alert_if_needed(
                tg_client=tg_client,
                tg_chat_id=tg_chat_id,
                count=count,
            )

        # Backoff после провала, чтобы не уходить в тугой цикл и дать инфраструктуре
        # шанс на восстановление (например, перезапуск browser_agent через supervisor).
        idx = min(count, len(_FAILURE_BACKOFF_SCHEDULE_SECONDS)) - 1
        backoff = (
            _FAILURE_BACKOFF_SCHEDULE_SECONDS[idx] if idx >= 0 else _FAILURE_BACKOFF_MAX_SECONDS
        )
        if backoff > 0:
            logger.info("Observer: пауза %.0fс перед следующей попыткой (count=%d)", backoff, count)
            await asyncio.sleep(backoff)

    async def handle_unknown_modal_artifacts(
        self,
        artifacts: list,
        *,
        tg_client,
        tg_chat_id: str,
    ) -> None:
        """Отправить TG-алерт о неизвестной модалке (счётчик провалов НЕ увеличивается).

        Алерт шлётся не чаще раза в 30 минут, чтобы не флудить при повторных
        срабатываниях одной и той же неизвестной модалки.

        Args:
            artifacts: список артефактов модалки из ScanResult.
            tg_client: TelegramBotClient (может быть None).
            tg_chat_id: ID чата.
        """
        if not artifacts:
            return

        paths = ", ".join(str(a) for a in artifacts[:10])
        logger.warning(
            "Observer: обнаружена неизвестная модалка в Ads Manager. Артефакты: %s", paths
        )

        if not tg_client or not tg_chat_id:
            return

        # Дедуп: не чаще раза в 30 минут
        now = datetime.now(UTC)
        if self._last_modal_alert_at is not None:
            elapsed = (now - self._last_modal_alert_at).total_seconds()
            if elapsed < _CRITICAL_ALERT_COOLDOWN_SECONDS:
                logger.info(
                    "Observer: алерт о неизвестной модалке подавлен (последний %.0f мин назад)",
                    elapsed / 60,
                )
                return

        text = (
            "👀 <b>В Ads Manager появилось окно, которое бот не умеет закрывать</b>\n\n"
            "Что произошло: текущий цикл скана пропущен, чтобы не нажать что-то лишнее.\n"
            "Что делать: открой Ads Manager, закрой окно вручную — следующий цикл "
            "пойдёт нормально.\n\n"
            "<i>Если это окно появляется регулярно — пришли скриншот, добавлю его "
            "в каталог известных модалок.</i>\n"
            f"<i>Артефакты для разбора: {paths}</i>"
        )
        try:
            ops_thread_id = await _load_ops_thread_id()
            await tg_client.send_message(
                chat_id=tg_chat_id, text=text, message_thread_id=ops_thread_id
            )
            self._last_modal_alert_at = now
        except Exception:
            logger.exception("Observer: не удалось отправить алерт о неизвестной модалке в TG")

    async def _send_critical_alert_if_needed(
        self,
        *,
        tg_client,
        tg_chat_id: str,
        count: int,
    ) -> None:
        """Отправить крит-алерт один раз при переходе на count=4, затем раз в 30 мин."""
        now = datetime.now(UTC)
        if self._last_critical_alert_at is not None:
            elapsed = (now - self._last_critical_alert_at).total_seconds()
            if elapsed < _CRITICAL_ALERT_COOLDOWN_SECONDS:
                logger.error(
                    "Observer: %d подряд провалов цикла (крит-алерт уже отправлен %.0f мин назад)",
                    count,
                    elapsed / 60,
                )
                return

        if not tg_client or not tg_chat_id:
            logger.error(
                "Observer: %d подряд провалов цикла. TG-клиент недоступен — крит-алерт не отправлен.",
                count,
            )
            self._last_critical_alert_at = now
            return

        request_id = str(uuid.uuid4())
        text = (
            "🚨 <b>Сканер не оживает</b>\n\n"
            f"После {count} подряд неудачных циклов мягкое и жёсткое восстановление "
            "браузера не помогло. Похоже, проблема снаружи: проверь, что Vision-профиль "
            "запущен и доступен.\n"
            f"<i>request_id={request_id}</i>"
        )
        try:
            ops_thread_id = await _load_ops_thread_id()
            await tg_client.send_message(
                chat_id=tg_chat_id, text=text, message_thread_id=ops_thread_id
            )
            self._last_critical_alert_at = now
            logger.error(
                "Observer: крит-алерт отправлен в TG (count=%d, request_id=%s)",
                count,
                request_id,
            )
            # Авто-диагностика — отдельным сообщением, не блокирует основной алерт.
            asyncio.create_task(
                self._send_ai_diagnosis(
                    tg_client=tg_client,
                    tg_chat_id=tg_chat_id,
                    count=count,
                    request_id=request_id,
                    ops_thread_id=ops_thread_id,
                )
            )
        except Exception:
            logger.exception("Observer: не удалось отправить крит-алерт в TG")
            # Всё равно ставим временну́ю метку, чтобы не флудить при неработающем TG
            self._last_critical_alert_at = now

    async def _send_ai_diagnosis(
        self,
        *,
        tg_client,
        tg_chat_id: str,
        count: int,
        request_id: str,
        ops_thread_id: int | None,
    ) -> None:
        """Запросить у AI диагноз и отправить отдельным сообщением."""
        try:
            from core.ai_assistant.diagnostics import diagnose_alert

            diagnosis = await diagnose_alert(
                alert_key=f"observer:scanner_dead:{count}",
                context=f"observer не оживает после {count} провалов; request_id={request_id}",
                log_name="observer.log",
            )
            if not diagnosis:
                return
            body = f"🤖 <b>AI-диагноз</b>\n\n{diagnosis}"
            await tg_client.send_message(
                chat_id=tg_chat_id, text=body, message_thread_id=ops_thread_id
            )
        except Exception:
            logger.warning("Observer: AI-диагноз не отправлен", exc_info=True)
