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

    async def handle_unknown_modal_artifacts(
        self,
        artifacts: list,
        *,
        tg_client,
        tg_chat_id: str,
    ) -> None:
        """Отправить TG-алерт о неизвестной модалке (счётчик провалов НЕ увеличивается).

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

        text = (
            "⚠️ Замечена неизвестная модалка в Ads Manager.\n"
            f"Артефакты: {paths}.\n"
            "Скан текущего цикла приостановлен."
        )
        try:
            await tg_client.send_message(chat_id=tg_chat_id, text=text)
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
            f"🚨 Observer: {count} подряд провала цикла. "
            "Soft и hard reconnect не помогли. "
            "Требуется ручное вмешательство: "
            "supervisorctl restart browser_agent observer_worker. "
            f"request_id={request_id}"
        )
        try:
            await tg_client.send_message(chat_id=tg_chat_id, text=text)
            self._last_critical_alert_at = now
            logger.error(
                "Observer: крит-алерт отправлен в TG (count=%d, request_id=%s)",
                count,
                request_id,
            )
        except Exception:
            logger.exception("Observer: не удалось отправить крит-алерт в TG")
            # Всё равно ставим временну́ю метку, чтобы не флудить при неработающем TG
            self._last_critical_alert_at = now
