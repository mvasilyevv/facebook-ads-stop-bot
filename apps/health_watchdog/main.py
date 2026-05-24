# -*- coding: utf-8 -*-
"""Health Watchdog — периодически проверяет здоровье воркеров и реагирует на отклонения."""

from __future__ import annotations

import asyncio
import http.client
import logging
import os
import socket
import time
from dataclasses import dataclass
from xmlrpc.client import ServerProxy, Transport

from sqlalchemy import select

from apps.api.routers.health import HealthDetails, collect_health_details
from apps.health_watchdog.alert_state import AlertCooldown, IncidentTracker
from core.alerts.send import send_telegram_via_queue
from core.config import get_settings
from core.db import get_session_factory
from core.metrics import WORKER_HEARTBEAT_AGE
from core.models import ObserverSettings, TelegramSettings
from core.observer.runtime_status import update_worker_heartbeat
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

# Порог wall-clock jump: 3 × интервал = 90с по умолчанию.
# Используется для детекции wake from sleep (macOS закрыли крышку).
_WAKE_JUMP_THRESHOLD_SECONDS = WATCHDOG_INTERVAL_SECONDS * 3

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


async def _is_scanning_enabled() -> bool:
    """Возвращает True, если сканирование включено в ObserverSettings.

    При ошибке доступа к БД считаем включённым (fail-open),
    чтобы не маскировать настоящие проблемы.
    """
    try:
        factory = get_session_factory()
        async with factory() as db:
            row = await db.scalar(
                select(ObserverSettings).where(ObserverSettings.singleton_key == "default")
            )
            if row is not None:
                return bool(row.is_scanning_enabled)
    except Exception as exc:
        logger.warning("Watchdog: не удалось прочитать ObserverSettings: %s", exc)
    return True


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


def _ops_chat_id(group_chat_id: str, settings: TelegramSettings | None) -> str:
    """Возвращает chat_id для системных ops-алертов.

    Приоритет: личка владельца (owner_telegram_user_id) → группа.
    Системные шумные сообщения уводим из общей супергруппы в DM владельцу.
    """
    if settings is not None:
        owner = (settings.owner_telegram_user_id or "").strip()
        if owner:
            return owner
    return group_chat_id


# --- Анти-флуд cooldown и трекер инцидентов ---


class _AlertCooldown(AlertCooldown):
    """Обёртка для обратной совместимости с тестами.

    Принимает 0 аргументов и использует общий _ALERT_COOLDOWN_SECONDS.
    """

    def __init__(self) -> None:
        super().__init__(_ALERT_COOLDOWN_SECONDS)


_cooldown = _AlertCooldown()
_incidents = IncidentTracker()


# --- Sleep-detector (wake from system sleep) ---


_last_iteration_monotonic: float | None = None


def _detect_wake_from_sleep() -> bool:
    """Детектит wall-clock jump между итерациями (системный sleep).

    Возвращает True, если monotonic-jump превысил _WAKE_JUMP_THRESHOLD_SECONDS.
    Всегда обновляет внутренний state, даже если jump зафиксирован.
    """
    global _last_iteration_monotonic
    now = time.monotonic()
    if _last_iteration_monotonic is None:
        _last_iteration_monotonic = now
        return False
    elapsed = now - _last_iteration_monotonic
    _last_iteration_monotonic = now
    return elapsed > _WAKE_JUMP_THRESHOLD_SECONDS


def _reset_sleep_detector() -> None:
    """Сбрасывает sleep-detector (нужен для unit-тестов)."""
    global _last_iteration_monotonic
    _last_iteration_monotonic = None


# --- Отправка TG-алерта ---


@dataclass
class _Incident:
    """Один обнаруженный инцидент для batched-отправки."""

    key: str
    label: str  # человекочитаемое имя ("Сканер", "Vision API")
    detail: str  # короткое описание ("heartbeat 240с", "timeout")
    classic_text: str  # полный текст для отправки одиночным алертом


async def _send_alert(
    tg: TelegramBotClient,
    chat_id: str,
    text: str,
    key: str,
    message_thread_id: int | None = None,
) -> None:
    """Отправляет TG-алерт через Redis-очередь если cooldown истёк."""
    if not _cooldown.can_send(key):
        logger.debug("Watchdog: cooldown активен для ключа '%s', алерт пропущен", key)
        return
    try:
        # Критичные алерты воркера идут через Redis-очередь для надёжности
        await send_telegram_via_queue(
            chat_id=chat_id,
            text=text,
            fallback_client=tg,
            message_thread_id=message_thread_id,
        )
        _cooldown.mark_sent(key)
        _incidents.mark_open(key)
        logger.info("Watchdog: TG-алерт поставлен в очередь (ключ: %s)", key)
    except Exception as exc:
        logger.error("Watchdog: не удалось поставить TG-алерт в очередь: %s", exc)
        return

    # Авто-диагностика для эскалаций (если AI настроен)
    if _is_escalation_key(key):
        asyncio.create_task(
            _send_ai_diagnosis(tg, chat_id, key, text, message_thread_id=message_thread_id)
        )


async def _send_incidents_batch(
    tg: TelegramBotClient,
    chat_id: str,
    incidents: list[_Incident],
    message_thread_id: int | None = None,
) -> list[str]:
    """Отправляет инциденты единым сообщением (или классическим, если один).

    Возвращает список ключей, для которых cooldown реально применился и сообщение ушло.
    """
    pending = [inc for inc in incidents if _cooldown.can_send(inc.key)]
    if not pending:
        return []

    if len(pending) == 1:
        inc = pending[0]
        await _send_alert(
            tg,
            chat_id,
            inc.classic_text,
            inc.key,
            message_thread_id=message_thread_id,
        )
        return [inc.key]

    bullets = "\n".join(f"• {inc.label}: {inc.detail}" for inc in pending)
    summary_text = (
        f"🚨 <b>Система не в порядке ({len(pending)} компонента)</b>\n\n"
        f"{bullets}\n\n"
        "Пытаюсь автоматически перезапустить. Отдельное сообщение придёт, "
        "если восстановление не помогло."
    )
    try:
        await send_telegram_via_queue(
            chat_id=chat_id,
            text=summary_text,
            fallback_client=tg,
            message_thread_id=message_thread_id,
        )
    except Exception as exc:
        logger.error("Watchdog: не удалось отправить batched summary: %s", exc)
        return []

    sent_keys: list[str] = []
    for inc in pending:
        _cooldown.mark_sent(inc.key)
        _incidents.mark_open(inc.key)
        sent_keys.append(inc.key)
    logger.info("Watchdog: batched summary отправлен (%d инцидентов)", len(sent_keys))
    return sent_keys


async def _send_resolves_batch(
    tg: TelegramBotClient,
    chat_id: str,
    resolved_labels: list[tuple[str, str]],
    message_thread_id: int | None = None,
) -> None:
    """Отправляет auto-resolve сообщение (одиночное или summary)."""
    if not resolved_labels:
        return

    if len(resolved_labels) == 1:
        _, label = resolved_labels[0]
        text = f"✅ <b>{label} снова в порядке</b>"
    else:
        bullets = "\n".join(f"• {label}" for _, label in resolved_labels)
        text = f"✅ <b>Восстановлены компоненты ({len(resolved_labels)})</b>\n\n{bullets}"

    try:
        await send_telegram_via_queue(
            chat_id=chat_id,
            text=text,
            fallback_client=tg,
            message_thread_id=message_thread_id,
        )
        logger.info("Watchdog: auto-resolve отправлен (%d ключей)", len(resolved_labels))
    except Exception as exc:
        logger.error("Watchdog: не удалось отправить auto-resolve: %s", exc)


def _is_escalation_key(key: str) -> bool:
    """Эскалационные ключи — требуют AI-диагностики."""
    return (
        key.endswith("_after_restart")
        or key.endswith(":restart_failed")
        or key.endswith(":log_stale_after_restart")
    )


async def _send_ai_diagnosis(
    tg: TelegramBotClient,
    chat_id: str,
    alert_key: str,
    original_text: str,
    *,
    message_thread_id: int | None = None,
) -> None:
    """Запросить у AI диагноз и отправить отдельным сообщением (без cooldown)."""
    try:
        from core.ai_assistant.diagnostics import diagnose_alert

        diagnosis = await diagnose_alert(alert_key=alert_key, context=original_text)
        if not diagnosis:
            return
        body = f"🤖 <b>AI-диагноз</b>\n\n{diagnosis}"
        await tg.send_message(chat_id=chat_id, text=body, message_thread_id=message_thread_id)
        logger.info("Watchdog: AI-диагноз отправлен для %s", alert_key)
    except Exception as exc:
        logger.warning("Watchdog: AI-диагноз не отправлен (%s): %s", alert_key, exc)


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


def _resolve_healthy_incidents(
    health: HealthDetails,
    scanning_enabled: bool,
) -> list[tuple[str, str]]:
    """Возвращает список (key, label) для инцидентов, которые сейчас healthy.

    Только ключи, которые были open в IncidentTracker.
    """
    resolved: list[tuple[str, str]] = []

    for key in _incidents.open_keys():
        # Парсим ключ. Формат: alert:<scope>:<reason>
        parts = key.split(":")
        if len(parts) < 3 or parts[0] != "alert":
            continue
        scope = parts[1]
        reason = parts[2]

        # Heartbeat stale для воркера
        if reason == "heartbeat_stale" or reason == "restart_failed":
            worker = health.workers.get(scope)
            if worker and worker.healthy:
                label = _WORKER_FRIENDLY_NAMES.get(scope, scope)
                resolved.append((key, label))
            continue

        # Log stale — наш ключ имеет scope = log_name
        if reason.startswith("log_stale"):
            # log_stale закрываем, только если сам лог снова растёт.
            # Здесь не можем проверить файл (нет path), поэтому опираемся
            # на supervisor (если воркер healthy → лог растёт).
            worker = health.workers.get(scope)
            if worker and worker.healthy:
                label = _WORKER_FRIENDLY_NAMES.get(scope, scope)
                resolved.append((key, label))
            continue

        # browser_agent
        if scope == "browser_agent":
            if health.browser_agent.healthy:
                resolved.append((key, "Браузер-агент"))
            continue

        # vision
        if scope == "vision":
            if health.vision.healthy:
                resolved.append((key, "Vision API"))
            continue

        # queues — если очереди разгребены
        if scope == "queues":
            if scanning_enabled and (
                health.queues.disable_running == 0 and health.queues.enable_running == 0
            ):
                resolved.append((key, "Очереди задач"))
            elif not scanning_enabled:
                # Скан выключен — считаем "разрешившимся", очереди простаивают намеренно.
                resolved.append((key, "Очереди задач"))
            continue

    return resolved


async def _run_iteration(tg: TelegramBotClient, chat_id: str) -> None:
    """Выполняет одну итерацию проверки health."""
    # Sleep-detector: если между итерациями произошёл wall-clock jump > 90с,
    # значит система спала. Пропускаем итерацию и сбрасываем cooldown,
    # чтобы на следующей итерации (через 30с) алерты ушли без задержки.
    if _detect_wake_from_sleep():
        logger.warning(
            "Watchdog: обнаружен системный sleep (jump > %dс), пропускаю итерацию",
            _WAKE_JUMP_THRESHOLD_SECONDS,
        )
        _cooldown.reset_all()
        return

    factory = get_session_factory()
    async with factory() as db:
        try:
            health: HealthDetails = await collect_health_details(db)
        except Exception as exc:
            logger.error("Watchdog: не удалось получить health-данные: %s", exc)
            return

    # --- Выставляем Prometheus-метрики возраста heartbeat'ов ---
    for _worker_name, _worker_health in health.workers.items():
        if _worker_health.heartbeat_age_seconds is not None:
            WORKER_HEARTBEAT_AGE.labels(worker=_worker_name).set(
                _worker_health.heartbeat_age_seconds
            )

    # --- Загружаем ops thread_id для всех системных алертов этой итерации ---
    _, tg_settings = await _get_telegram_settings()
    ops_thread_id = None  # forum-topic режим удалён
    # Системные ops-сообщения летят в личку владельца, чтобы не шуметь в супергруппе.
    ops_chat_id = _ops_chat_id(chat_id, tg_settings)

    # Если сканирование выключено пользователем — все воркеры в idle,
    # все ops-алерты подавляются (кроме health_watchdog, но он сам себя не алертит).
    scanning_enabled = await _is_scanning_enabled()
    if not scanning_enabled:
        logger.debug("Watchdog: сканирование выключено — алерты подавлены глобально")

    # --- Собираем инциденты этой итерации в один список (для batched-отправки) ---
    incidents: list[_Incident] = []

    # --- Рост лог-файлов ---
    log_stale_for_restart: list[tuple[str, str, str]] = []  # (log_path, log_name, alert_key)
    for log_path, log_name in _WATCHED_LOGS:
        if not scanning_enabled:
            continue
        if not _check_log_growth(log_path):
            alert_key = f"alert:{log_name}:log_stale"
            label = _WORKER_FRIENDLY_NAMES.get(log_name, log_name)
            classic_text = (
                f"📭 <b>{label} молчит</b>\n\n"
                f"Лог не обновлялся {_LOG_STALE_THRESHOLD // 60} минут — "
                "процесс мог зависнуть. Сейчас попробую перезапустить автоматически."
            )
            incidents.append(
                _Incident(
                    key=alert_key,
                    label=label.capitalize(),
                    detail=f"лог молчит {_LOG_STALE_THRESHOLD // 60} мин",
                    classic_text=classic_text,
                )
            )
            log_stale_for_restart.append((log_path, log_name, alert_key))

    # --- Воркеры с устаревшим heartbeat ---
    workers_to_restart: list[tuple[str, str, str]] = []  # (worker_name, supervisor_name, alert_key)
    if scanning_enabled:
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
            classic_text = (
                f"😴 <b>Воркер «{worker_label}» завис</b>\n\n"
                f"Не подавал признаков жизни {age_str}. Сейчас попробую перезапустить "
                "автоматически — отдельное сообщение придёт, если не получится."
            )
            incidents.append(
                _Incident(
                    key=alert_key,
                    label=worker_label.capitalize(),
                    detail=f"heartbeat {age_str}",
                    classic_text=classic_text,
                )
            )
            if supervisor_name:
                workers_to_restart.append((worker_name, supervisor_name, alert_key))
    else:
        logger.debug("Watchdog: heartbeat-алерты воркеров подавлены — сканирование выключено")

    # --- browser_agent gRPC unhealthy ---
    if not health.browser_agent.healthy and scanning_enabled:
        alert_key = "alert:browser_agent:grpc_unhealthy"
        error_detail = health.browser_agent.error or "нет деталей"
        classic_text = (
            "🌐 <b>Браузер-агент не отвечает</b>\n\n"
            "Сканер не может получить доступ к Ads Manager. Сейчас попробую "
            "перезапустить агент автоматически.\n"
            f"<i>Причина: {error_detail}</i>"
        )
        incidents.append(
            _Incident(
                key=alert_key,
                label="Браузер-агент",
                detail=error_detail,
                classic_text=classic_text,
            )
        )

    # --- Vision API unhealthy ---
    if not health.vision.healthy and scanning_enabled:
        alert_key = "alert:vision:unhealthy"
        error_detail = health.vision.error or "нет деталей"
        classic_text = (
            "🛰 <b>Vision (anti-detect) не отвечает</b>\n\n"
            "Не получается достучаться до Vision API. Без него браузер-агент "
            "не сможет открыть Ads Manager.\n\n"
            "Что делать: проверь, что приложение Vision запущено и подписка активна.\n"
            f"<i>Причина: {error_detail}</i>"
        )
        incidents.append(
            _Incident(
                key=alert_key,
                label="Vision API",
                detail=error_detail,
                classic_text=classic_text,
            )
        )

    # --- Зависшие RUNNING-задачи (симптом, только алерт) ---
    if scanning_enabled and (health.queues.disable_running > 0 or health.queues.enable_running > 0):
        alert_key = "alert:queues:stuck_running"
        classic_text = (
            "📦 <b>Задачи в очереди застряли</b>\n\n"
            f"Выключение: {health.queues.disable_running} задач(и) висят более "
            f"{_STUCK_TASK_THRESHOLD // 60} минут.\n"
            f"Включение: {health.queues.enable_running} задач(и).\n\n"
            "Что делать: загляни в дашборд → «Очереди задач». "
            "Обычно достаточно дождаться или перезапустить браузер-агент."
        )
        incidents.append(
            _Incident(
                key=alert_key,
                label="Очереди задач",
                detail=f"disable={health.queues.disable_running}, "
                f"enable={health.queues.enable_running}",
                classic_text=classic_text,
            )
        )

    # --- Auto-resolve: проверяем какие открытые инциденты теперь healthy ---
    resolved = _resolve_healthy_incidents(health, scanning_enabled)
    # Исключаем те, что прямо сейчас опять unhealthy — на всякий случай
    incident_keys_now = {inc.key for inc in incidents}
    resolved = [(k, label) for k, label in resolved if k not in incident_keys_now]
    if resolved:
        await _send_resolves_batch(tg, ops_chat_id, resolved, message_thread_id=ops_thread_id)
        for key, _ in resolved:
            _incidents.resolve(key)
            _cooldown.reset(key)  # после resolve следующий инцидент уходит без задержки

    # --- БД недоступна (критическое — алертим сразу, не через batched-логику) ---
    if not health.database.healthy:
        logger.critical("Watchdog: база данных недоступна — действий не предпринимаем")
        # БД-алерт мы не вкладываем в incidents, потому что без БД половина проверок
        # уже не сработала и кран затягивать дальше нечем.
        return

    if not incidents and not resolved:
        logger.debug("Watchdog: все компоненты здоровы")
        return

    if incidents:
        logger.warning("Watchdog: обнаружено инцидентов: %d", len(incidents))

    # --- Отправляем инциденты единым сообщением (batched) ---
    if incidents:
        await _send_incidents_batch(tg, ops_chat_id, incidents, message_thread_id=ops_thread_id)

    # --- Авто-восстановление через supervisor для воркеров ---
    for worker_name, supervisor_name, _alert_key in workers_to_restart:
        logger.warning(
            "Watchdog: перезапускаем '%s' через supervisor (воркер: %s)",
            supervisor_name,
            worker_name,
        )
        try:
            await restart_via_supervisor(supervisor_name)
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
                    ops_chat_id,
                    (
                        f"🚨 <b>Воркер «{worker_label}» не оживает</b>\n\n"
                        "Я попробовал перезапустить его автоматически — без результата. "
                        "Похоже, нужен внешний разбор: проверь логи в .logs/ и Vision-профиль."
                    ),
                    escalation_key,
                    message_thread_id=ops_thread_id,
                )
            else:
                logger.info("Watchdog: воркер '%s' восстановился после рестарта", worker_name)
        except Exception as exc:
            logger.error("Watchdog: ошибка при рестарте '%s': %s", supervisor_name, exc)

    # --- Авто-восстановление по log_stale ---
    for log_path, log_name, _alert_key in log_stale_for_restart:
        supervisor_name = _WORKER_TO_SUPERVISOR.get(log_name, log_name)
        try:
            await restart_via_supervisor(supervisor_name)
            await asyncio.sleep(_RESTART_VERIFY_DELAY)
            if not _check_log_growth(log_path):
                label = _WORKER_FRIENDLY_NAMES.get(log_name, log_name)
                await _send_alert(
                    tg,
                    ops_chat_id,
                    (
                        f"🚨 <b>{label} не оживает</b>\n\n"
                        "Я перезапустил процесс автоматически — лог всё ещё не "
                        "растёт. Похоже, нужен внешний разбор: проверь Vision и "
                        "доступность браузера."
                    ),
                    f"alert:{log_name}:log_stale_after_restart",
                    message_thread_id=ops_thread_id,
                )
        except Exception as exc:
            logger.error("Watchdog: ошибка авто-рестарта по log_stale (%s): %s", log_name, exc)

    # --- browser_agent: рестарт после алерта ---
    if not health.browser_agent.healthy and scanning_enabled:
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
                    ops_chat_id,
                    (
                        "🚨 <b>Браузер-агент не оживает</b>\n\n"
                        "Я перезапустил его автоматически — без результата. "
                        "Скорее всего, проблема снаружи: проверь, что Vision-профиль "
                        "запущен и подписка активна."
                    ),
                    "alert:browser_agent:restart_failed",
                    message_thread_id=ops_thread_id,
                )
        except Exception as exc:
            logger.error("Watchdog: ошибка при рестарте browser_agent: %s", exc)


# --- Точка входа ---


async def main() -> None:
    """Главный цикл health_watchdog."""
    from core.logging import setup_logging

    setup_logging("health_watchdog")
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
            # Обновляем heartbeat в самом начале итерации — до любой работы,
            # которая может занять десятки секунд (рестарты через supervisor).
            await update_worker_heartbeat("health_watchdog", status="running")
            chat_id, _ = await _get_telegram_settings()
            in_grace = time.monotonic() < startup_grace_until
            if in_grace:
                logger.info("Watchdog: grace-период после старта, алерты подавлены")
            try:
                if not in_grace:
                    await _run_iteration(tg, chat_id)
            except Exception as exc:
                logger.exception("Watchdog: необработанная ошибка в итерации: %s", exc)
            await asyncio.sleep(WATCHDOG_INTERVAL_SECONDS)
    finally:
        await tg.close()
        logger.info("Health Watchdog остановлен")
