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
from xmlrpc.client import ServerProxy, Transport

from sqlalchemy import select

from apps.api.routers.health import HealthDetails, collect_health_details
from core.config import get_settings
from core.db import get_session_factory
from core.models import TelegramSettings
from core.observer.runtime_status import update_worker_heartbeat
from core.telegram.client import TelegramBotClient
from core.telegram.delivery import resolve_thread_id

logger = logging.getLogger(__name__)

# Интервал проверки (секунды)
WATCHDOG_INTERVAL_SECONDS = int(os.getenv("HEALTH_WATCHDOG_INTERVAL_SECONDS", "30"))

# Cooldown для повторных алертов (секунды = 10 минут)
_ALERT_COOLDOWN_SECONDS = 600

# Пауза после рестарта перед повторной проверкой
_RESTART_VERIFY_DELAY = 60

# Порог "лог не растёт" (секунды = 5 минут)
_LOG_STALE_THRESHOLD = 300

# Логи для мониторинга роста.
# Только observer.log — у него есть строки каждые 15 секунд при работе.
# browser_agent.log по дизайну молчит между событиями (модалки, старт),
# его здоровье отдельно проверяется через gRPC ping в health.browser_agent.healthy.
_WATCHED_LOGS = [
    (".logs/observer.log", "observer"),
]

# Маппинг имён воркеров из HealthDetails → имена программ supervisor
_WORKER_TO_SUPERVISOR: dict[str, str] = {
    "observer": "observer_worker",
    "telegram_poller": "telegram_poller",
    "disable": "disable_worker",
    "enable": "enable_worker",
    "enable_recommendation": "enable_recommendation_worker",
}

# Человекочитаемые имена воркеров для текстов алертов
_WORKER_FRIENDLY_NAMES: dict[str, str] = {
    "observer": "сканер объявлений",
    "telegram_poller": "Telegram-бот",
    "disable": "выключатель объявлений",
    "enable": "включатель объявлений",
    "enable_recommendation": "генератор рекомендаций",
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


# --- Получение настроек Telegram из БД ---


async def _get_telegram_settings() -> tuple[str, TelegramSettings | None]:
    """Возвращает (chat_id, TelegramSettings | None) из БД или config как fallback."""
    try:
        factory = get_session_factory()
        async with factory() as db:
            row = await db.scalar(
                select(TelegramSettings).where(TelegramSettings.singleton_key == "default")
            )
            if row and row.chat_id:
                return row.chat_id, row
    except Exception as exc:
        logger.warning("Watchdog: не удалось прочитать TelegramSettings из БД: %s", exc)
    return get_settings().telegram_chat_id, None


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


async def _send_alert(
    tg: TelegramBotClient,
    chat_id: str,
    text: str,
    key: str,
    message_thread_id: int | None = None,
) -> None:
    """Отправляет TG-алерт если cooldown истёк."""
    if not _cooldown.can_send(key):
        logger.debug("Watchdog: cooldown активен для ключа '%s', алерт пропущен", key)
        return
    try:
        await tg.send_message(chat_id=chat_id, text=text, message_thread_id=message_thread_id)
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

    # --- Загружаем ops thread_id для всех системных алертов этой итерации ---
    _, tg_settings = await _get_telegram_settings()
    ops_thread_id = resolve_thread_id("ops", tg_settings) if tg_settings else None

    # --- Рост лог-файлов проверяем всегда (независимо от overall_healthy) ---
    for log_path, log_name in _WATCHED_LOGS:
        if not _check_log_growth(log_path):
            alert_key = f"alert:{log_name}:log_stale"
            await _send_alert(
                tg,
                chat_id,
                (
                    f"📭 <b>{log_name} молчит</b>\n\n"
                    f"Лог-файл не обновлялся {_LOG_STALE_THRESHOLD // 60} минут — "
                    "процесс либо завис, либо ничего не делает.\n"
                    f"Что делать: <code>supervisorctl restart {log_name}</code> "
                    "или загляни в логи.\n"
                    f"<i>Файл: {log_path}</i>"
                ),
                alert_key,
                message_thread_id=ops_thread_id,
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
        worker_label = _WORKER_FRIENDLY_NAMES.get(worker_name, worker_name)
        alert_text = (
            f"😴 <b>Воркер «{worker_label}» завис</b>\n\n"
            f"Не подавал признаков жизни {age_str}. Сейчас попробую перезапустить "
            "автоматически — отдельное сообщение придёт, если не получится."
        )
        await _send_alert(tg, chat_id, alert_text, alert_key, message_thread_id=ops_thread_id)

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
                    worker_label = _WORKER_FRIENDLY_NAMES.get(worker_name, worker_name)
                    await _send_alert(
                        tg,
                        chat_id,
                        (
                            f"🚨 <b>Воркер «{worker_label}» не оживает</b>\n\n"
                            "Я попробовал перезапустить его — без результата.\n\n"
                            "Что делать: открой терминал и выполни:\n"
                            f"<code>supervisorctl restart {supervisor_name}</code>\n"
                            "Если не помогло — посмотри логи в .logs/."
                        ),
                        escalation_key,
                        message_thread_id=ops_thread_id,
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
                "🌐 <b>Браузер-агент не отвечает</b>\n\n"
                "Сканер не может получить доступ к Ads Manager. Сейчас попробую "
                "перезапустить агент автоматически.\n"
                f"<i>Причина: {error_detail}</i>"
            ),
            alert_key,
            message_thread_id=ops_thread_id,
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
                        "🚨 <b>Браузер-агент не оживает</b>\n\n"
                        "Я попробовал перезапустить его — без результата.\n\n"
                        "Что делать:\n"
                        "1. Проверь, что Vision-профиль запущен.\n"
                        "2. Открой терминал и выполни:\n"
                        "<code>supervisorctl restart browser_agent</code>\n"
                        "3. Если не помогло — посмотри .logs/browser_agent.log."
                    ),
                    "alert:browser_agent:restart_failed",
                    message_thread_id=ops_thread_id,
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
                "🛰 <b>Vision (anti-detect) не отвечает</b>\n\n"
                "Не получается достучаться до Vision API. Без него браузер-агент "
                "не сможет открыть Ads Manager.\n\n"
                "Что делать: проверь, что приложение Vision запущено и подписка активна.\n"
                f"<i>Причина: {error_detail}</i>"
            ),
            alert_key,
            message_thread_id=ops_thread_id,
        )

    # --- Зависшие RUNNING-задачи (симптом, только алерт) ---
    if health.queues.disable_running > 0 or health.queues.enable_running > 0:
        alert_key = "alert:queues:stuck_running"
        await _send_alert(
            tg,
            chat_id,
            (
                "📦 <b>Задачи в очереди застряли</b>\n\n"
                f"Выключение: {health.queues.disable_running} задач(и) висят более "
                f"{_STUCK_TASK_THRESHOLD // 60} минут.\n"
                f"Включение: {health.queues.enable_running} задач(и).\n\n"
                "Что делать: загляни в дашборд → «Очереди задач». "
                "Обычно достаточно дождаться или перезапустить браузер-агент."
            ),
            alert_key,
            message_thread_id=ops_thread_id,
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

    # Grace-период после старта: не алертим первые 90 секунд, чтобы дать время воркерам подняться
    startup_grace_until = time.monotonic() + 90.0

    try:
        while True:
            chat_id, _ = await _get_telegram_settings()
            in_grace = time.monotonic() < startup_grace_until
            if in_grace:
                logger.info("Watchdog: grace-период после старта, алерты подавлены")
            try:
                if not in_grace:
                    await _run_iteration(tg, chat_id)
            except Exception as exc:
                logger.exception("Watchdog: необработанная ошибка в итерации: %s", exc)
            await update_worker_heartbeat("health_watchdog", status="running")
            await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)
    finally:
        await tg.close()
        logger.info("Health Watchdog остановлен")
