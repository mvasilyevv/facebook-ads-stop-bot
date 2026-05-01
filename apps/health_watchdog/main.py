# -*- coding: utf-8 -*-
"""Health Watchdog — периодически проверяет здоровье воркеров и реагирует на отклонения."""

from __future__ import annotations

import asyncio
import http.client
import logging
import os
import socket
import sys
import time
from datetime import UTC, datetime
from xmlrpc.client import ServerProxy, Transport

from sqlalchemy import select

from apps.api.routers.health import HealthDetails, collect_health_details
from core.config import get_settings
from core.db import get_session_factory
from core.models import TelegramSettings
from core.telegram.client import TelegramBotClient

logger = logging.getLogger(__name__)

# Интервал проверки (секунды)
WATCHDOG_INTERVAL_SECONDS = int(os.getenv("HEALTH_WATCHDOG_INTERVAL_SECONDS", "30"))

# Cooldown для повторных алертов (секунды = 10 минут)
_ALERT_COOLDOWN_SECONDS = 600

# Пауза после рестарта перед повторной проверкой
_RESTART_VERIFY_DELAY = 60

# Порог "лог не растёт" (секунды = 5 минут)
_LOG_STALE_THRESHOLD = 300

# Логи для мониторинга роста
_WATCHED_LOGS = [
    (".logs/observer.log", "observer"),
    (".logs/browser_agent.log", "browser_agent"),
]

# Маппинг имён воркеров из HealthDetails → имена программ supervisor
_WORKER_TO_SUPERVISOR: dict[str, str] = {
    "observer": "observer_worker",
    "telegram_poller": "telegram_poller",
    "disable": "disable_worker",
    "enable": "enable_worker",
    "enable_recommendation": "enable_recommendation_worker",
}

# Порог зависшей RUNNING-задачи (секунды = 10 минут)
_STUCK_TASK_THRESHOLD = 600


# --- Supervisor XML-RPC через Unix-сокет ---


class _UnixStreamHTTPConnection(http.client.HTTPConnection):
    """HTTP-соединение через Unix-сокет (для supervisor XML-RPC)."""

    def __init__(self, path: str) -> None:
        super().__init__("localhost")
        self._path = path

    def connect(self) -> None:
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self._path)
        self.sock = sock


class _UnixStreamTransport(Transport):
    """XML-RPC Transport через Unix-сокет."""

    def __init__(self, path: str) -> None:
        super().__init__()
        self._path = path

    def make_connection(self, host: str) -> _UnixStreamHTTPConnection:
        return _UnixStreamHTTPConnection(self._path)


def supervisor_proxy(
    socket_path: str = "/tmp/fb_agent_supervisor.sock",
) -> ServerProxy:
    """Возвращает XML-RPC прокси для управления supervisor."""
    return ServerProxy("http://localhost", transport=_UnixStreamTransport(socket_path))


def _supervisor_restart(name: str) -> None:
    """Синхронный рестарт программы через supervisor XML-RPC."""
    proxy = supervisor_proxy()
    try:
        proxy.supervisor.stopProcess(name)
        logger.info("Watchdog: supervisor остановил программу '%s'", name)
    except Exception as exc:
        logger.warning("Watchdog: не удалось остановить '%s': %s", name, exc)
    try:
        proxy.supervisor.startProcess(name)
        logger.info("Watchdog: supervisor запустил программу '%s'", name)
    except Exception as exc:
        logger.error("Watchdog: не удалось запустить '%s': %s", name, exc)
        raise


async def restart_via_supervisor(name: str) -> None:
    """Асинхронный рестарт программы через supervisor (синхронный вызов в потоке)."""
    await asyncio.to_thread(_supervisor_restart, name)


# --- Получение chat_id из БД ---


async def _get_telegram_chat_id() -> str:
    """Возвращает chat_id из TelegramSettings в БД или из config как fallback."""
    try:
        factory = get_session_factory()
        async with factory() as db:
            row = await db.scalar(
                select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
            )
            if row and row.chat_id:
                return row.chat_id
    except Exception as exc:
        logger.warning("Watchdog: не удалось прочитать chat_id из БД: %s", exc)
    return get_settings().telegram_chat_id


# --- Анти-флуд cooldown ---


class _AlertCooldown:
    """Хранит время последней отправки алерта по ключу и проверяет cooldown."""

    def __init__(self) -> None:
        self._last_sent: dict[str, float] = {}

    def can_send(self, key: str) -> bool:
        last = self._last_sent.get(key, 0.0)
        return (time.monotonic() - last) >= _ALERT_COOLDOWN_SECONDS

    def mark_sent(self, key: str) -> None:
        self._last_sent[key] = time.monotonic()


_cooldown = _AlertCooldown()


# --- Отправка TG-алерта ---


async def _send_alert(tg: TelegramBotClient, chat_id: str, text: str, key: str) -> None:
    """Отправляет TG-алерт если cooldown истёк."""
    if not _cooldown.can_send(key):
        logger.debug("Watchdog: cooldown активен для ключа '%s', алерт пропущен", key)
        return
    try:
        await tg.send_message(chat_id=chat_id, text=text)
        _cooldown.mark_sent(key)
        logger.info("Watchdog: TG-алерт отправлен (ключ: %s)", key)
    except Exception as exc:
        logger.error("Watchdog: не удалось отправить TG-алерт: %s", exc)


# --- Проверка роста лог-файлов ---


def _check_log_growth(log_path: str) -> bool:
    """Возвращает True если лог рос в последние _LOG_STALE_THRESHOLD секунд."""
    full_path = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), log_path)
    try:
        stat = os.stat(full_path)
        age = time.time() - stat.st_mtime
        return age < _LOG_STALE_THRESHOLD
    except FileNotFoundError:
        return True  # Файла нет — не алертим, воркер мог не запускаться
    except Exception as exc:
        logger.warning("Watchdog: ошибка проверки лога '%s': %s", log_path, exc)
        return True


# --- Основная логика одной итерации ---


async def _run_iteration(tg: TelegramBotClient, chat_id: str) -> None:
    """Выполняет одну итерацию проверки health."""
    factory = get_session_factory()
    async with factory() as db:
        try:
            health: HealthDetails = await collect_health_details(db)
        except Exception as exc:
            logger.error("Watchdog: не удалось получить health-данные: %s", exc)
            return

    # --- Рост лог-файлов проверяем всегда (независимо от overall_healthy) ---
    for log_path, log_name in _WATCHED_LOGS:
        if not _check_log_growth(log_path):
            alert_key = f"alert:{log_name}:log_stale"
            await _send_alert(
                tg,
                chat_id,
                (
                    f"⚠️ <b>Watchdog: {log_name} не пишет логи</b>\n"
                    f"Файл {log_path} не обновлялся "
                    f"{_LOG_STALE_THRESHOLD // 60} минут.\n"
                    f"Время: {datetime.now(UTC).strftime('%H:%M:%S UTC')}"
                ),
                alert_key,
            )

    if health.overall_healthy:
        logger.debug("Watchdog: все компоненты здоровы")
        return

    logger.warning("Watchdog: обнаружены проблемы со здоровьем системы")

    # --- БД недоступна ---
    if not health.database.healthy:
        logger.critical("Watchdog: база данных недоступна — действий не предпринимаем")
        return  # Без БД нельзя продолжать

    # --- Воркеры с устаревшим heartbeat ---
    for worker_name, worker_health in health.workers.items():
        if worker_health.healthy:
            continue
        if worker_name == "health_watchdog":
            continue  # Сам себя не перезапускаем

        supervisor_name = _WORKER_TO_SUPERVISOR.get(worker_name)
        age_str = (
            f"{worker_health.heartbeat_age_seconds:.0f}с"
            if worker_health.heartbeat_age_seconds is not None
            else "неизвестно"
        )

        alert_key = f"alert:{worker_name}:heartbeat_stale"
        alert_text = (
            f"⚠️ <b>Watchdog: воркер {worker_name} не отвечает</b>\n"
            f"Возраст heartbeat: {age_str}\n"
            f"Время: {datetime.now(UTC).strftime('%H:%M:%S UTC')}"
        )
        await _send_alert(tg, chat_id, alert_text, alert_key)

        if supervisor_name:
            logger.warning(
                "Watchdog: перезапускаем '%s' через supervisor (воркер: %s)",
                supervisor_name,
                worker_name,
            )
            try:
                await restart_via_supervisor(supervisor_name)
                # Ждём и перепроверяем
                logger.info(
                    "Watchdog: ожидаем %dс после рестарта '%s'",
                    _RESTART_VERIFY_DELAY,
                    supervisor_name,
                )
                await asyncio.sleep(_RESTART_VERIFY_DELAY)
                factory2 = get_session_factory()
                async with factory2() as db2:
                    health2 = await collect_health_details(db2)
                wh2 = health2.workers.get(worker_name)
                if wh2 and not wh2.healthy:
                    logger.critical(
                        "Watchdog: рестарт '%s' не помог — воркер всё ещё нездоров",
                        supervisor_name,
                    )
                    escalation_key = f"alert:{worker_name}:restart_failed"
                    await _send_alert(
                        tg,
                        chat_id,
                        (
                            f"🚨 <b>Watchdog: рестарт {supervisor_name} не помог</b>\n"
                            f"Воркер {worker_name} всё ещё не отвечает после рестарта.\n"
                            f"Требуется ручное вмешательство!"
                        ),
                        escalation_key,
                    )
                else:
                    logger.info("Watchdog: воркер '%s' восстановился после рестарта", worker_name)
            except Exception as exc:
                logger.error("Watchdog: ошибка при рестарте '%s': %s", supervisor_name, exc)

    # --- browser_agent gRPC unhealthy ---
    if not health.browser_agent.healthy:
        alert_key = "alert:browser_agent:grpc_unhealthy"
        error_detail = health.browser_agent.error or "нет деталей"
        await _send_alert(
            tg,
            chat_id,
            (
                f"⚠️ <b>Watchdog: browser_agent недоступен</b>\n"
                f"Ошибка: {error_detail}\n"
                f"Время: {datetime.now(UTC).strftime('%H:%M:%S UTC')}"
            ),
            alert_key,
        )
        logger.warning("Watchdog: перезапускаем browser_agent через supervisor")
        try:
            await restart_via_supervisor("browser_agent")
            await asyncio.sleep(_RESTART_VERIFY_DELAY)
            factory3 = get_session_factory()
            async with factory3() as db3:
                health3 = await collect_health_details(db3)
            if not health3.browser_agent.healthy:
                logger.critical("Watchdog: рестарт browser_agent не помог")
                await _send_alert(
                    tg,
                    chat_id,
                    (
                        "🚨 <b>Watchdog: рестарт browser_agent не помог</b>\n"
                        "browser_agent всё ещё недоступен после рестарта.\n"
                        "Требуется ручное вмешательство!"
                    ),
                    "alert:browser_agent:restart_failed",
                )
        except Exception as exc:
            logger.error("Watchdog: ошибка при рестарте browser_agent: %s", exc)

    # --- Vision API unhealthy ---
    if not health.vision.healthy:
        alert_key = "alert:vision:unhealthy"
        error_detail = health.vision.error or "нет деталей"
        await _send_alert(
            tg,
            chat_id,
            (
                f"⚠️ <b>Watchdog: Vision API недоступен</b>\n"
                f"Ошибка: {error_detail}\n"
                f"Время: {datetime.now(UTC).strftime('%H:%M:%S UTC')}"
            ),
            alert_key,
        )

    # --- Зависшие RUNNING-задачи (симптом, только алерт) ---
    if health.queues.disable_running > 0 or health.queues.enable_running > 0:
        alert_key = "alert:queues:stuck_running"
        await _send_alert(
            tg,
            chat_id,
            (
                f"⚠️ <b>Watchdog: зависшие задачи в очереди</b>\n"
                f"disable RUNNING: {health.queues.disable_running}, "
                f"enable RUNNING: {health.queues.enable_running}\n"
                f"Задачи в состоянии RUNNING более {_STUCK_TASK_THRESHOLD // 60} минут."
            ),
            alert_key,
        )


# --- Точка входа ---


async def main() -> None:
    """Главный цикл health_watchdog."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-7s [%(name)s] %(message)s",
        stream=sys.stdout,
    )
    logger.info(
        "Health Watchdog запущен, интервал=%dс, cooldown=%dс",
        WATCHDOG_INTERVAL_SECONDS,
        _ALERT_COOLDOWN_SECONDS,
    )

    cfg = get_settings()
    tg = TelegramBotClient(bot_token=cfg.telegram_bot_token)

    try:
        while True:
            chat_id = await _get_telegram_chat_id()
            try:
                await _run_iteration(tg, chat_id)
            except Exception as exc:
                logger.exception("Watchdog: необработанная ошибка в итерации: %s", exc)
            await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)
    finally:
        await tg.close()
        logger.info("Health Watchdog остановлен")
