#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Supervisor eventlistener: отправляет Telegram-алерт при сбое процесса.

Реализует протокол supervisord eventlistener (stdin/stdout READY/RESULT).
"""

from __future__ import annotations

import asyncio
import sys
import time
from typing import Any

# Антифлуд: processname -> timestamp последней отправки
_COOLDOWN_SECONDS = 300  # 5 минут

# Интересующие события
_WATCHED_EVENTS = {
    "PROCESS_STATE_FATAL",
    "PROCESS_STATE_BACKOFF",
    "PROCESS_STATE_EXITED",
}


# ---------------------------------------------------------------------------
# Чистые функции (тестируемы без IO)
# ---------------------------------------------------------------------------


def parse_header(line: str) -> dict[str, str]:
    """Разбирает строку заголовка eventlistener-протокола в dict.

    Пример строки:
        "ver:3.0 server:supervisor serial:1 pool:crashmail poolserial:1 eventname:PROCESS_STATE_FATAL len:65"
    """
    result: dict[str, str] = {}
    for token in line.strip().split():
        if ":" in token:
            key, _, value = token.partition(":")
            result[key] = value
    return result


def parse_payload(blob: str) -> dict[str, str]:
    """Разбирает тело события (те же key:value через пробел).

    Пример:
        "processname:observer_worker groupname:observer_worker from_state:BACKOFF"
    """
    result: dict[str, str] = {}
    for token in blob.strip().split():
        if ":" in token:
            key, _, value = token.partition(":")
            result[key] = value
    return result


def format_alert_text(event: dict[str, Any]) -> str:
    """Форматирует текст Telegram-алерта на русском языке."""
    name = event.get("processname", "?")
    eventname = event.get("eventname", "?")
    from_state = event.get("from_state", "?")
    return (
        f"🚨 supervisor: процесс <b>{name}</b> перешёл в состояние "
        f"<b>{eventname}</b> (из {from_state}). "
        f"Подробности в .logs/supervisord.log"
    )


class CooldownTracker:
    """Контролирует антифлуд: одно сообщение на процесс не чаще раза в N секунд."""

    def __init__(self, cooldown_seconds: float = _COOLDOWN_SECONDS) -> None:
        self._cooldown = cooldown_seconds
        self._last_sent: dict[str, float] = {}

    def should_send(self, processname: str) -> bool:
        """Возвращает True, если алерт для процесса можно отправить сейчас."""
        now = time.monotonic()
        last = self._last_sent.get(processname, 0.0)
        return (now - last) >= self._cooldown

    def record_sent(self, processname: str) -> None:
        """Записывает факт отправки алерта."""
        self._last_sent[processname] = time.monotonic()


# ---------------------------------------------------------------------------
# Telegram-отправка (sync обёртка через asyncio.run)
# ---------------------------------------------------------------------------


def _send_telegram(text: str) -> None:
    """Синхронно отправляет сообщение через Redis-очередь (или напрямую как fallback)."""
    from core.alerts.send import send_telegram_via_queue

    from core.config import get_settings
    from core.telegram.client import TelegramBotClient

    settings = get_settings()
    if not settings.telegram_bot_token or not settings.telegram_chat_id:
        print(
            "supervisor_crashmail: токен или chat_id не настроены, пропускаем отправку",
            file=sys.stderr,
        )
        return

    # Создаём клиент заранее — передаём как fallback_client, чтобы тесты
    # могли мокировать TelegramBotClient и видеть вызовы send_message.
    client = TelegramBotClient(bot_token=settings.telegram_bot_token)

    async def _send() -> None:
        try:
            # Crash-алерт от supervisor — идёт через Redis-очередь для надёжной доставки
            await send_telegram_via_queue(
                chat_id=settings.telegram_chat_id,
                text=text,
                fallback_client=client,
            )
        finally:
            await client.close()

    asyncio.run(_send())


# ---------------------------------------------------------------------------
# Основной цикл eventlistener
# ---------------------------------------------------------------------------


def main() -> None:
    """Главный цикл supervisor eventlistener."""
    cooldown = CooldownTracker()

    while True:
        # 1. Сообщаем supervisord, что готовы принять событие
        sys.stdout.write("READY\n")
        sys.stdout.flush()

        # 2. Читаем строку заголовка
        try:
            header_line = sys.stdin.readline()
        except Exception as exc:
            print(f"supervisor_crashmail: ошибка чтения заголовка: {exc}", file=sys.stderr)
            sys.stdout.write("RESULT 2\nOK")
            sys.stdout.flush()
            continue

        if not header_line:
            # EOF — supervisord завершился
            break

        try:
            header = parse_header(header_line)
        except Exception as exc:
            print(f"supervisor_crashmail: ошибка разбора заголовка: {exc}", file=sys.stderr)
            sys.stdout.write("RESULT 2\nOK")
            sys.stdout.flush()
            continue

        # 3. Читаем payload нужной длины
        payload_len = int(header.get("len", "0"))
        try:
            payload_blob = sys.stdin.read(payload_len)
        except Exception as exc:
            print(f"supervisor_crashmail: ошибка чтения payload: {exc}", file=sys.stderr)
            sys.stdout.write("RESULT 2\nOK")
            sys.stdout.flush()
            continue

        # 4. Обрабатываем нужные события
        eventname = header.get("eventname", "")
        if eventname in _WATCHED_EVENTS:
            try:
                payload = parse_payload(payload_blob)
                payload["eventname"] = eventname
                processname = payload.get("processname", "?")

                if cooldown.should_send(processname):
                    text = format_alert_text(payload)
                    try:
                        _send_telegram(text)
                        cooldown.record_sent(processname)
                    except Exception as exc:
                        print(
                            f"supervisor_crashmail: не удалось отправить Telegram-алерт: {exc}",
                            file=sys.stderr,
                        )
                else:
                    print(
                        f"supervisor_crashmail: алерт для '{processname}' подавлен cooldown'ом",
                        file=sys.stderr,
                    )
            except Exception as exc:
                print(f"supervisor_crashmail: ошибка обработки события: {exc}", file=sys.stderr)

        # 5. Подтверждаем обработку события
        sys.stdout.write("RESULT 2\nOK")
        sys.stdout.flush()


if __name__ == "__main__":
    main()
