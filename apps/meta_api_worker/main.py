# -*- coding: utf-8 -*-
"""meta_api_worker main loop.

Этап 5: реальная диспетчеризация mutations через AuditedMetaApiClient +
dispatch_mutation. До Этапа 5 здесь была заглушка с NotImplementedError.

Состояние процесса:
- heartbeat: Redis worker:heartbeat:meta_api TTL 60s
- reconcile: делегирован каноническому reconciler_worker (общий по task_type, с bump
  attempt_count) — локальный reconcile-loop убран, чтобы не было двух reconciler'ов
- idle: spinning poll с asyncio.sleep
- graceful: SIGTERM/SIGINT → завершить текущий цикл и закрыть ресурсы

Маршрутизация ошибок:
- PermanentError / TokenInvalidError / NotFoundError / PermissionError → mark_failed (retry бесполезен)
- RateLimitedError / TemporaryError / SessionUnavailableError → requeue_for_retry
- NotImplementedError (новый mutation_kind без handler) → mark_failed
- MutationValidationError (осознанная валидационная ошибка в handler'е) → mark_failed
- CreateCampaignPartialError → mark_failed + лог осиротевших id (нужна ручная чистка)
- голый ValueError (неожиданный, баг в коде) → requeue (защитный retry, логируется как аномалия)
- любое другое Exception → requeue (защитный retry на transient)
- ИСКЛЮЧЕНИЕ для необратимых kinds (create_campaign/duplicate_campaign): transient/
  ValueError/Exception → mark_failed (НЕ requeue), т.к. ответ мог потеряться после
  коммита Meta и retry создал бы дубль кампании. См. _IRREVERSIBLE_KINDS.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import redis.asyncio as redis_asyncio
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from core.db import WORKER_ENGINE_KWARGS
from core.meta_api.audit import AuditedMetaApiClient
from core.meta_api.autostop_alert import (
    maybe_alert_autostop_channel_down,
    record_autostop_success,
)
from core.meta_api.client import MetaApiClient
from core.meta_api.errors import (
    MutationValidationError,
    NotFoundError,
    PermanentError,
    RateLimitedError,
    SessionUnavailableError,
    TemporaryError,
    TokenInvalidError,
)
from core.meta_api.errors import (
    PermissionError as MetaPermissionError,
)
from core.meta_api.fsm_sync import is_deactivating_bulk, sync_fsm_after_mutation
from core.meta_api.mutations import dispatch_mutation
from core.meta_api.mutations.create_campaign import CreateCampaignPartialError
from core.meta_api.mutations.duplicate_campaign import DuplicateCampaignPartialError
from core.meta_api.ownership import check_mutation_ownership, load_owner_tag
from core.meta_api.queue import (
    claim_pending_task,
    mark_task_failed,
    mark_task_succeeded,
    requeue_task,
)
from core.meta_api.schemas import IRREVERSIBLE_MUTATION_KINDS, MetaMutationPayload
from core.observer.queries import load_scanning_enabled
from core.pubsub import CHANNEL_TASK_CHANGED
from core.tasks.queue import Task, touch_task_running
from core.telegram.worker_notify import notify_owners

logger = logging.getLogger("meta_api_worker")

WORKER_NAME = "meta_api"
HEARTBEAT_KEY = f"worker:heartbeat:{WORKER_NAME}"
HEARTBEAT_TTL_SECONDS = 60
IDLE_SLEEP_SECONDS = 5

# MID-10 (аудит 02.07): интервал touch-heartbeat'а долгих mutation. Reconciler метит
# 'running' старше 30 мин (RECONCILER_STUCK_TIMEOUT_MIN) в 'retrying' по updated_at.
# Долгий исполнитель (upload видео, медленный Meta) без освежения updated_at был бы
# украден → дубль/двойной открут. Освежаем каждые 5 мин (<< 30-мин таймаут) через
# core.tasks.queue.touch_task_running. Env-override для тестов/тюнинга.
_TASK_TOUCH_INTERVAL_SECONDS = int(os.environ.get("META_API_TASK_TOUCH_INTERVAL_SEC", str(5 * 60)))

# requested_by авто-стопа (observer → pause_ad). Совпадает с writers._create_pause_mutation.
_AUTO_STOP_REQUESTED_BY = "bot_auto_stop"

# Конфиг CRITICAL-алерта «канал auto-stop мёртв» (см. core/meta_api/autostop_alert.py).
# Money-сигнал: после N подряд сетевых фейлов pause_ad шлём ОДИН алерт «чини Vision»,
# а не молча ретраим до 72 попыток (~6ч). Дефолты переопределяются из env.
_ALERT_THRESHOLD = int(os.environ.get("AUTOSTOP_ALERT_THRESHOLD", "3"))
_ALERT_WINDOW_SEC = int(os.environ.get("AUTOSTOP_ALERT_WINDOW_SEC", str(30 * 60)))
_ALERT_DEDUP_SEC = int(os.environ.get("AUTOSTOP_ALERT_DEDUP_SEC", str(30 * 60)))

# Per-ad эскалация недоставленной паузы (см. core/meta_api/autostop_alert.py): если конкретная
# auto-stop pause_ad висит недоставленной дольше N секунд — точечный «выключи вручную» с именем
# объявления и спендом. Дополняет channel-level CRITICAL. Дефолты переопределяются из env.
_UNDELIVERED_AFTER_SEC = int(os.environ.get("AUTOSTOP_UNDELIVERED_AFTER_SEC", str(10 * 60)))
_UNDELIVERED_DEDUP_SEC = int(os.environ.get("AUTOSTOP_UNDELIVERED_DEDUP_SEC", str(60 * 60)))


@dataclass(frozen=True)
class AutostopAlertContext:
    """Параметры CRITICAL-алерта auto-stop, прокинутые из main_loop в process_one_task.

    engine используется для рассылки через notify_recipients (всем активным recipients).
    """

    engine: Any  # AsyncEngine для recipients-рассылки
    threshold: int = _ALERT_THRESHOLD
    window_seconds: int = _ALERT_WINDOW_SEC
    dedup_ttl_seconds: int = _ALERT_DEDUP_SEC


async def _publish_task_changed(
    redis_client: redis_asyncio.Redis | None,
    *,
    task_id: int,
    task_type: str,
    status: str,
) -> None:
    """Best-effort publish изменения статуса задачи в fb_agent:task:changed."""
    if redis_client is None:
        return
    try:
        payload = json.dumps(
            {
                "task_id": task_id,
                "task_type": task_type,
                "status": status,
                "timestamp": datetime.now(UTC).isoformat(),
            },
            ensure_ascii=False,
        )
        await redis_client.publish(CHANNEL_TASK_CHANGED, payload)
    except Exception:
        logger.warning("meta_api_worker: не удалось publish task:changed task_id=%s", task_id)


def _get_database_url() -> str:
    from core.config import get_settings

    return get_settings().database_url


def _get_redis_url() -> str:
    return os.environ.get("REDIS_URL", "redis://localhost:6380/0")


def _build_meta_client(engine: AsyncEngine) -> AuditedMetaApiClient:
    """Сконструировать клиент Marketing API с auditing.

    host/port — из env с дефолтами под локальный browser-agent.
    """
    return AuditedMetaApiClient(
        engine=engine,
        initiated_by=WORKER_NAME,
        host=os.environ.get("BROWSER_AGENT_HOST", "localhost"),
        port=int(os.environ.get("BROWSER_AGENT_GRPC_PORT", "50051")),
    )


async def execute_mutation(
    payload: MetaMutationPayload,
    *,
    client: MetaApiClient,
) -> dict[str, Any]:
    """Исполнить mutation через dispatch_mutation.

    Доменные ошибки Meta пробрасываются как есть — process_one_task маршрутизирует.
    """
    return await dispatch_mutation(client, payload)


async def _touch_loop(engine: AsyncEngine, task_id: int, interval_seconds: float) -> None:
    """Фоновый touch-таск: пока mutation исполняется, каждые interval_seconds освежает
    updated_at задачи (MID-10) — защита от кражи reconciler'ом долгой mutation.

    Останавливается через cancel() из _execute_with_touch по завершении mutation.
    Если touch вернул False (задача уже не 'running' — украдена/закрыта) — цикл выходит,
    незачем биться о закрытую строку. Ошибки БД проглатываются (touch best-effort, он
    не должен ронять исполнение mutation).
    """
    while True:
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            raise
        try:
            still_running = await touch_task_running(engine, task_id=task_id)
        except Exception:  # noqa: BLE001 — touch best-effort, не роняем mutation
            logger.debug("meta_api: touch task id=%s упал (продолжаю)", task_id, exc_info=True)
            continue
        if not still_running:
            logger.debug(
                "meta_api: touch task id=%s — строка уже не 'running', останавливаю touch-цикл",
                task_id,
            )
            return


async def _execute_with_touch(
    engine: AsyncEngine,
    task_id: int,
    payload: MetaMutationPayload,
    *,
    client: MetaApiClient,
    touch_interval_seconds: float = _TASK_TOUCH_INTERVAL_SECONDS,
) -> dict[str, Any]:
    """execute_mutation с фоновым heartbeat-touch updated_at (MID-10).

    Долгие mutation (upload видео, медленная обработка Meta) могут исполняться дольше
    30-мин reconciler-таймаута. Без освежения updated_at reconcile_stuck_running увёл бы
    задачу в 'retrying' → повторное исполнение = дубль/двойной открут. Пока идёт
    execute_mutation, фоновый _touch_loop держит updated_at свежим. По завершении
    (успех/исключение) touch-таск отменяется. Исключения mutation пробрасываются как есть.
    """
    touch_task = asyncio.create_task(_touch_loop(engine, task_id, touch_interval_seconds))
    try:
        return await execute_mutation(payload, client=client)
    finally:
        touch_task.cancel()
        try:
            await touch_task
        except asyncio.CancelledError:
            pass


# ====================== классификация ошибок ======================


# Permanent ошибки → mark_failed (retry не имеет смысла).
# MutationValidationError(ValueError) — осознанная ошибка валидации payload в handler'е.
# Голый ValueError (случайный, из-за бага) — НЕ здесь, он попадёт в отдельную ветку.
_PERMANENT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    TokenInvalidError,
    NotFoundError,
    MetaPermissionError,
    PermanentError,
    NotImplementedError,
    MutationValidationError,
)

# Temporary ошибки → requeue_for_retry.
_TEMPORARY_EXCEPTIONS: tuple[type[BaseException], ...] = (
    RateLimitedError,
    SessionUnavailableError,
    TemporaryError,
)

# Необратимые mutations: создают новые объекты в Meta (кампании/копии). Если ответ
# потерян УЖЕ ПОСЛЕ коммита на стороне Meta (gRPC DEADLINE/UNAVAILABLE, битый JSON,
# ValueError на постобработке успешного ответа), повторный вызов = ДУБЛЬ кампании +
# двойной открут бюджета. idempotency_key (на enqueue) от retry той же строки не
# защищает. Поэтому transient/неожиданные ошибки для них НЕ ретраим, а уводим в
# mark_failed с явным сигналом «проверь Meta вручную».
# Единый источник правды — core.meta_api.schemas.IRREVERSIBLE_MUTATION_KINDS (его же
# использует reconciler-крэш-путь). Локальный алиас — для краткости в этом модуле.
_IRREVERSIBLE_KINDS: frozenset[str] = IRREVERSIBLE_MUTATION_KINDS


async def _fail_irreversible(
    engine: AsyncEngine,
    task: Task,
    payload: MetaMutationPayload,
    exc: BaseException,
    *,
    reason: str,
) -> None:
    """Завершить необратимую mutation как failed (без retry) при transient-ошибке.

    Money-safety: ответ Meta мог потеряться после коммита → повторный вызов создал бы
    дубль кампании. Помечаем failed с явным error — задача видна в дашборде, оператор
    проверяет Meta вручную.
    """
    logger.error(
        "meta_api: task id=%s kind=%s — необратимая mutation, ошибка (%s) возможно ПОСЛЕ "
        "коммита Meta. НЕ ретраим (риск дубля кампании). ПРОВЕРЬ Meta вручную! err=%r",
        task.id,
        payload.mutation_kind,
        reason,
        exc,
    )
    applied = await mark_task_failed(
        engine,
        task_id=task.id,
        error=f"irreversible_no_retry ({reason}): проверь Meta вручную — возможен дубль: {exc!r}",
    )
    if not applied:
        logger.warning(
            "meta_api: task id=%s mark_failed (irreversible) не применился — гонка", task.id
        )


# ====================== асимметричный стоп ======================

# mutation_kind, которые ВЫКЛЮЧАЮТ открут (снижают трату) — разрешены даже на паузе.
_DEACTIVATING_KINDS = frozenset({"pause_ad", "pause_campaign"})

# Money-мутации остановки рекламы: при финальном провале владелец должен узнать.
_PAUSE_KINDS = frozenset({"pause_ad", "bulk_status_change"})


def is_mutation_success(result: Any) -> bool:
    """True если результат mutation — логический успех (R3).

    Batch-конверт Graph API даёт HTTP 200 без exception, но пер-саб ошибки лежат в теле.
    Два handler'а возвращают «логический провал» без raise: bulk_status_change при полном
    отказе Meta (success_result хардкодит success=True, но succeeded==0 & failed>0) и
    duplicate_campaign при провале copy/rename (явный success=False). Без этой проверки
    worker метил такие задачи succeeded → money-fail DM не уходил, бюджет тёк.

    Контракт:
    - не-dict / None → True (handler не вернул структуру — не наша забота, обычный успех);
    - result['success'] is False → провал;
    - bulk-форма (есть 'succeeded'/'failed'): succeeded==0 и failed>0 → провал (полный отказ);
    - иначе → успех (в т.ч. partial bulk с succeeded>0 — он succeeded, но алертит отдельно).
    """
    if not isinstance(result, dict):
        return True
    if result.get("success") is False:
        return False
    succeeded = result.get("succeeded")
    failed = result.get("failed")
    if succeeded is not None and failed is not None:
        try:
            if int(succeeded) == 0 and int(failed) > 0:
                return False
        except (TypeError, ValueError):
            return True
    return True


def is_partial_bulk_failure(result: Any) -> bool:
    """True если bulk применился частично (succeeded>0, но failed>0).

    Для partial mark_succeeded остаётся корректным (FSM-sync метит только modified_ids),
    но владелец должен узнать о недовыключенных объявлениях через money-fail DM.
    """
    if not isinstance(result, dict):
        return False
    succeeded = result.get("succeeded")
    failed = result.get("failed")
    if succeeded is None or failed is None:
        return False
    try:
        return int(succeeded) > 0 and int(failed) > 0
    except (TypeError, ValueError):
        return False


def _is_activating_mutation(payload: MetaMutationPayload) -> bool:
    """True если mutation ВКЛЮЧАЕТ/тратит (на паузе сканирования откладывается).

    Асимметричный стоп пропускает только ВЫКЛЮЧАЮЩИЕ действия (они снижают риск
    открута), всё остальное на паузе блокирует. Выключающие: pause_ad/pause_campaign
    и bulk_status_change с action pause/paused. Всё прочее (activate_*, bulk activate,
    create_campaign, duplicate_campaign, set_adset_budget, set_ad_creative,
    custom_audience) — «не выключающее» → True → откладываем (money-safe: на стопе
    кабинет не трогаем сверх выключения).
    """
    kind = payload.mutation_kind
    if kind in _DEACTIVATING_KINDS:
        return False
    if kind == "bulk_status_change":
        # Обе формы bulk: сокращённая (action=pause) и полная (status=PAUSED).
        # Выключающий bulk разрешён даже на паузе → не активирующий.
        return not is_deactivating_bulk(getattr(payload, "params", None) or {})
    return True


async def _alert_money_fail(
    engine,
    redis_client,
    *,
    payload: MetaMutationPayload,
    requested_by: str,
    error: str,
    kind_label: str,
) -> None:
    """Финальный провал money-мутации (пауза/bulk-стоп) → DM owner'ам. Best-effort.

    Алертим только денежные действия: pause_ad/bulk_status_change (стоп рекламы).
    Бюджет, кастомные аудитории, создание — не алертим здесь.
    """
    if payload.mutation_kind not in _PAUSE_KINDS:
        return
    actor = "Авто-стоп" if requested_by == _AUTO_STOP_REQUESTED_BY else "Пауза"
    text = (
        f"❌ <b>{actor} не сработал окончательно</b>\n"
        f"fb_ad_id=<code>{payload.target_id}</code> ({kind_label})\n"
        f"Ошибка: {error[:200]}\n"
        f"Отключи объявление вручную."
    )
    await notify_owners(
        engine,
        redis_client,
        category="money_fail",
        text=text,
        dedup_key=f"auto_stop_fail:{payload.target_id}",
        dedup_ttl_seconds=3600,
    )


async def process_one_task(
    engine: AsyncEngine,
    task: Task,
    *,
    client: MetaApiClient | None = None,
    redis_client: redis_asyncio.Redis | None = None,
    alert_ctx: AutostopAlertContext | None = None,
) -> None:
    """Полный жизненный цикл одной задачи.

    client опционален — если None, mutation не исполнится (для тестов с моками).
    В production main_loop всегда передаёт реальный AuditedMetaApiClient.
    """
    try:
        payload = MetaMutationPayload.from_dict(task.payload)
    except (KeyError, ValueError) as exc:
        logger.error("Невалидный payload в task id=%s: %s", task.id, exc)
        applied = await mark_task_failed(engine, task_id=task.id, error=f"invalid payload: {exc}")
        if not applied:
            logger.warning(
                "meta_api: task id=%s mark_failed (invalid payload) не применился "
                "— гонка с другим воркером",
                task.id,
            )
        return

    if client is None:
        # Защитная ветка для случая когда клиент не подан (старые тесты).
        # В production main_loop всегда передаёт клиент.
        applied = await mark_task_failed(
            engine,
            task_id=task.id,
            error="MetaApiClient не доступен в worker (Vision-сессия?)",
        )
        if not applied:
            logger.warning(
                "meta_api: task id=%s mark_failed (no client) не применился "
                "— гонка с другим воркером",
                task.id,
            )
        return

    # Асимметричный стоп: на паузе сканирования откладываем АКТИВИРУЮЩИЕ mutations
    # (activate/bulk activate/create/duplicate/budget/...), пропуская только
    # ВЫКЛЮЧАЮЩИЕ (pause_*, bulk pause) — они снижают риск открута. Отложенная
    # задача уходит в retry и исполнится после снятия паузы; если пауза длится
    # дольше лимита попыток — зафейлится (autostart, инициированный до паузы,
    # осознанно отменяется пользовательским стопом).
    if _is_activating_mutation(payload) and not await load_scanning_enabled(engine):
        retried = await requeue_task(
            engine,
            task=task,
            error="scanning_paused: активирующая mutation отложена до снятия паузы",
        )
        if retried:
            logger.info(
                "meta_api: task id=%s (%s) отложена — сканирование на паузе (асимметричный стоп)",
                task.id,
                payload.mutation_kind,
            )
        else:
            logger.warning(
                "meta_api: task id=%s (%s) отменена — пауза дольше лимита попыток",
                task.id,
                payload.mutation_kind,
            )
        return

    # Owner-scoping на исполнении: last-line-of-defense против действий с ЧУЖИМИ
    # объявлениями в шаренном кабинете. Резолвим target → campaign_name из каталога
    # и сверяем owner_tag. Строгая политика: чужое не трогаем (permanent fail); своё,
    # но ещё не в каталоге (скан отстал) — ВЫКЛЮЧАЮЩИЕ в requeue (скан догонит),
    # ВКЛЮЧАЮЩИЕ в fail. owner_tag пуст → гейт пропускает всё (фильтр выключен).
    owner_tag = await load_owner_tag(engine)
    ownership = await check_mutation_ownership(engine, payload, owner_tag=owner_tag)
    if not ownership.allowed:
        if ownership.not_found and not _is_activating_mutation(payload):
            retried = await requeue_task(
                engine, task=task, error=f"owner_scoping_not_found: {ownership.reason}"
            )
            if retried:
                logger.info(
                    "meta_api: task id=%s отложена — цель не в каталоге, скан догонит (%s)",
                    task.id,
                    ownership.reason,
                )
            else:
                logger.warning(
                    "meta_api: task id=%s owner_scoping not_found — исчерпаны попытки: %s",
                    task.id,
                    ownership.reason,
                )
            return
        applied = await mark_task_failed(
            engine, task_id=task.id, error=f"owner_scoping_reject: {ownership.reason}"
        )
        if applied:
            logger.warning(
                "meta_api: task id=%s ОТКЛОНЕНА owner-scoping (чужое/неизвестное): %s foreign=%s",
                task.id,
                ownership.reason,
                ownership.foreign_ids,
            )
        else:
            logger.warning(
                "meta_api: task id=%s owner_scoping mark_failed не применился — гонка",
                task.id,
            )
        return

    logger.info(
        "meta_api: исполняю task id=%s kind=%s target=%s",
        task.id,
        payload.mutation_kind,
        payload.target_id,
    )

    try:
        # MID-10: heartbeat-touch долгой mutation. Пока идёт исполнение, фоновый таск
        # освежает updated_at задачи — иначе reconciler украл бы running >30 мин → дубль.
        result = await _execute_with_touch(engine, task.id, payload, client=client)
        # R3: handler мог вернуть «логический провал» БЕЗ exception (Batch HTTP 200,
        # пер-саб ошибки в теле). bulk_status_change при полном отказе Meta хардкодит
        # success=True с succeeded==0; duplicate_campaign отдаёт success=False. Без этой
        # проверки задача метилась succeeded, money-fail DM не уходил, бюджет тёк.
        if not is_mutation_success(result):
            err = f"mutation_logical_fail: handler вернул провал без exception result={result!r}"
            applied = await mark_task_failed(engine, task_id=task.id, error=err[:500])
            if not applied:
                logger.warning(
                    "meta_api: task id=%s mark_failed (logical fail) не применился — гонка",
                    task.id,
                )
            else:
                logger.error(
                    "meta_api: task id=%s kind=%s — логический провал mutation (без exception), "
                    "mark_failed. ПРОВЕРЬ вручную! result=%r",
                    task.id,
                    payload.mutation_kind,
                    result,
                )
            # Money-мутация (pause/bulk) провалилась — owner должен узнать (как в except).
            await _alert_money_fail(
                engine,
                redis_client,
                payload=payload,
                requested_by=getattr(task, "requested_by", ""),
                error=err,
                kind_label=payload.mutation_kind,
            )
            return
        applied = await mark_task_succeeded(engine, task_id=task.id, result=result)
        if not applied:
            # Race: другой воркер уже закрыл задачу после reconciler-таймаута.
            # Mutation в Meta мы уже исполнили — повторно не делаем (return).
            logger.warning(
                "meta_api: task id=%s mark_succeeded не применился "
                "(status != running) — гонка с другим воркером, пропускаю",
                task.id,
            )
            return
        logger.info("meta_api: task id=%s succeeded", task.id)
        # Publish изменения статуса в Redis-канал (best-effort)
        await _publish_task_changed(
            redis_client,
            task_id=task.id,
            task_type=task.task_type,
            status="succeeded",
        )
        # FSM-sync: привести ad_alert_state к результату mutation. Идемпотентно и
        # best-effort (не роняет succeeded-контракт). Закрывает money-пробел —
        # без этого FSM застревал в stop_sent при auto-stop через API. result прокидываем
        # для bulk (H2): метим FSM только по реально применённым id (modified_ids).
        await sync_fsm_after_mutation(engine, payload, result)
        # R3 partial: bulk применился частично (succeeded>0, failed>0). FSM-sync корректен
        # (метит только modified_ids), задача succeeded, но часть объявлений НЕ выключилась —
        # owner должен узнать через money-fail DM, иначе недовыключенные тратят бюджет.
        if is_partial_bulk_failure(result):
            logger.warning(
                "meta_api: task id=%s kind=%s — bulk применился ЧАСТИЧНО "
                "(succeeded=%s failed=%s), часть объявлений не выключена",
                task.id,
                payload.mutation_kind,
                result.get("succeeded"),
                result.get("failed"),
            )
            await _alert_money_fail(
                engine,
                redis_client,
                payload=payload,
                requested_by=getattr(task, "requested_by", ""),
                error=(
                    f"bulk частично провалился: succeeded={result.get('succeeded')} "
                    f"failed={result.get('failed')} — проверь невыключенные вручную"
                ),
                kind_label=payload.mutation_kind,
            )
        # Канал auto-stop жив (mutation дошла) → сброс счётчика подряд-фейлов и дедупа,
        # чтобы следующий outage снова мог поднять CRITICAL (re-arm). Best-effort.
        if redis_client is not None:
            await record_autostop_success(redis_client)
        return
    except CreateCampaignPartialError as exc:
        # Batch API не атомарен: часть объектов уже создана в Meta.
        # Логируем осиротевшие id — оператор должен удалить их вручную.
        logger.error(
            "meta_api: task id=%s create_campaign partial fail — "
            "осиротевшие объекты в Meta, нужна ручная чистка! "
            "created_ids=%s failed_steps=%s",
            task.id,
            exc.created_ids,
            exc.failed_steps,
        )
        applied = await mark_task_failed(
            engine,
            task_id=task.id,
            error=f"partial_fail: created_ids={exc.created_ids!r} failed={exc.failed_steps!r}",
        )
        if not applied:
            logger.warning(
                "meta_api: task id=%s mark_failed (partial) не применился "
                "— гонка с другим воркером",
                task.id,
            )
        # Partial fail create_campaign — не money-стоп, _PAUSE_KINDS проверит.
        await _alert_money_fail(
            engine,
            redis_client,
            payload=payload,
            requested_by=getattr(task, "requested_by", ""),
            error=str(exc),
            kind_label=payload.mutation_kind,
        )
        return
    except DuplicateCampaignPartialError as exc:
        # MID-4: copy прошёл, rename упал → копия осиротела в Meta. Метим failed БЕЗ
        # retry (retry создал бы вторую копию — двойной открут). Логируем осиротевший
        # id для ручной проверки/переименования. Контракт как у CreateCampaignPartialError.
        logger.error(
            "meta_api: task id=%s duplicate_campaign partial fail — "
            "копия создана и осиротела в Meta, нужна ручная проверка! "
            "created_ids=%s failed_steps=%s",
            task.id,
            exc.created_ids,
            exc.failed_steps,
        )
        applied = await mark_task_failed(
            engine,
            task_id=task.id,
            error=f"duplicate_partial_fail: created_ids={exc.created_ids!r} "
            f"failed={exc.failed_steps!r}",
        )
        if not applied:
            logger.warning(
                "meta_api: task id=%s mark_failed (duplicate partial) не применился "
                "— гонка с другим воркером",
                task.id,
            )
        # duplicate_campaign не в _PAUSE_KINDS — _alert_money_fail сам отфильтрует (no-op),
        # но зовём единообразно с create_campaign веткой на случай расширения _PAUSE_KINDS.
        await _alert_money_fail(
            engine,
            redis_client,
            payload=payload,
            requested_by=getattr(task, "requested_by", ""),
            error=str(exc),
            kind_label=payload.mutation_kind,
        )
        return
    except _PERMANENT_EXCEPTIONS as exc:
        applied = await mark_task_failed(engine, task_id=task.id, error=repr(exc))
        if not applied:
            logger.warning(
                "meta_api: task id=%s mark_failed не применился "
                "(status != running) — гонка с другим воркером, пропускаю",
                task.id,
            )
        else:
            logger.warning("meta_api: task id=%s → permanent fail: %s", task.id, exc)
        # Токен истёк — отдельный дедуплицированный алерт «нужен re-login Vision».
        if isinstance(exc, TokenInvalidError):
            await notify_owners(
                engine,
                redis_client,
                category="token_invalid",
                text=(
                    "⚠️ <b>Marketing API: токен истёк</b>\n"
                    "Зайди в Vision и обнови токен (re-login Facebook)."
                ),
                dedup_key="meta_token_invalid",
                dedup_ttl_seconds=3600,
            )
        # Money-мутация (pause/bulk) финально провалилась — owner должен знать.
        await _alert_money_fail(
            engine,
            redis_client,
            payload=payload,
            requested_by=getattr(task, "requested_by", ""),
            error=repr(exc),
            kind_label=payload.mutation_kind,
        )
        return
    except _TEMPORARY_EXCEPTIONS as exc:
        # Необратимые kinds не ретраим: transient мог прилететь после коммита Meta → дубль.
        if payload.mutation_kind in _IRREVERSIBLE_KINDS:
            await _fail_irreversible(engine, task, payload, exc, reason="temporary")
            return
        retried = await requeue_task(engine, task=task, error=repr(exc))
        if retried:
            logger.warning("meta_api: task id=%s → retrying (temporary): %s", task.id, exc)
        else:
            logger.error("meta_api: task id=%s → exhausted retries (temporary): %s", task.id, exc)
            # Исчерпаны все попытки → финальный провал money-мутации (pause).
            await _alert_money_fail(
                engine,
                redis_client,
                payload=payload,
                requested_by=getattr(task, "requested_by", ""),
                error=f"exhausted retries: {exc!r}",
                kind_label=payload.mutation_kind,
            )
        # Money-сигнал: auto-stop pause_ad не доходит до Meta из-за мёртвого Vision-канала
        # (code=-2 Failed to fetch). После N подряд таких фейлов — ОДИН CRITICAL в TG
        # «чини Vision», а не молчаливый ретрай до 72 попыток. Best-effort.
        if (
            redis_client is not None
            and alert_ctx is not None
            and getattr(task, "requested_by", "") == _AUTO_STOP_REQUESTED_BY
        ):
            await maybe_alert_autostop_channel_down(
                redis_client,
                exc=exc,
                fb_ad_id=payload.target_id,
                engine=alert_ctx.engine,
                threshold=alert_ctx.threshold,
                window_seconds=alert_ctx.window_seconds,
                dedup_ttl_seconds=alert_ctx.dedup_ttl_seconds,
            )
        return
    except ValueError as exc:
        # Необратимые kinds: ValueError мог прийти на постобработке УЖЕ успешного ответа
        # (парсинг id/batch-тела) → retry создал бы дубль. Уводим в failed.
        if payload.mutation_kind in _IRREVERSIBLE_KINDS:
            await _fail_irreversible(engine, task, payload, exc, reason="value_error_postprocess")
            return
        # Голый ValueError — скорее всего баг в коде или неожиданный Graph-ответ.
        # Не mark_failed (без retry потеряем задачу навсегда), а requeue с аномалия-логом.
        logger.error(
            "meta_api: task id=%s неожиданный ValueError (возможно баг) — requeue: %s",
            task.id,
            exc,
            exc_info=True,
        )
        retried = await requeue_task(engine, task=task, error=f"unexpected_value_error: {exc!r}")
        if retried:
            logger.warning("meta_api: task id=%s → retrying (unexpected ValueError)", task.id)
        else:
            logger.error(
                "meta_api: task id=%s → final fail (unexpected ValueError, retries exhausted)",
                task.id,
            )
            # Исчерпаны все попытки → финальный провал money-мутации (pause).
            await _alert_money_fail(
                engine,
                redis_client,
                payload=payload,
                requested_by=getattr(task, "requested_by", ""),
                error=f"exhausted retries (ValueError): {exc!r}",
                kind_label=payload.mutation_kind,
            )
        return
    except Exception as exc:  # noqa: BLE001 — защитная сетка на неклассифицированное
        # Необратимые kinds: неклассифицированная ошибка после возможного коммита → не ретраим.
        if payload.mutation_kind in _IRREVERSIBLE_KINDS:
            await _fail_irreversible(engine, task, payload, exc, reason="unknown")
            return
        retried = await requeue_task(engine, task=task, error=repr(exc))
        if retried:
            logger.warning("meta_api: task id=%s → retrying (unknown): %s", task.id, exc)
        else:
            logger.error("meta_api: task id=%s → final fail (unknown): %s", task.id, exc)
            # Исчерпаны все попытки → финальный провал money-мутации (pause).
            await _alert_money_fail(
                engine,
                redis_client,
                payload=payload,
                requested_by=getattr(task, "requested_by", ""),
                error=f"exhausted retries (unknown): {exc!r}",
                kind_label=payload.mutation_kind,
            )


# ====================== sub-loops ======================


async def heartbeat_loop(redis_client: redis_asyncio.Redis, stop: asyncio.Event) -> None:
    """Периодически обновляет worker:heartbeat:meta_api с TTL 60s."""
    interval = HEARTBEAT_TTL_SECONDS / 2
    while not stop.is_set():
        try:
            await redis_client.set(HEARTBEAT_KEY, "alive", ex=HEARTBEAT_TTL_SECONDS)
        except Exception:  # noqa: BLE001
            logger.exception("heartbeat: ошибка записи в Redis")
        try:
            await asyncio.wait_for(stop.wait(), timeout=interval)
        except asyncio.TimeoutError:
            pass


async def task_loop(
    engine: AsyncEngine,
    stop: asyncio.Event,
    *,
    client: MetaApiClient,
    redis_client: redis_asyncio.Redis | None = None,
    alert_ctx: AutostopAlertContext | None = None,
) -> None:
    """Главный цикл claim → execute → mark."""
    while not stop.is_set():
        try:
            claim = await claim_pending_task(engine)
        except Exception:  # noqa: BLE001
            logger.exception("ошибка claim_pending_task")
            try:
                await asyncio.wait_for(stop.wait(), timeout=IDLE_SLEEP_SECONDS)
            except asyncio.TimeoutError:
                pass
            continue

        if claim.queue_empty or claim.task is None:
            # Волна 2/E: на холостом ходу обновляем кэш TZ кабинетов (троттлинг 6ч внутри).
            # Off auto-stop path — observer не трогаем. Best-effort, ошибки не ронят цикл.
            if redis_client is not None:
                try:
                    from core.meta_api.account_tz import maybe_refresh_account_tz

                    await maybe_refresh_account_tz(engine, redis_client, client)
                except Exception:  # noqa: BLE001
                    logger.debug("account_tz warmup пропущен", exc_info=True)
                # Money: per-ad эскалация недоставленной паузы (канал Vision завис, авто-исцеление
                # не помогло) — точечный «выключи вручную» с именем ad и спендом. Троттл внутри.
                try:
                    from core.meta_api.autostop_alert import escalate_undelivered_autostop_pauses

                    await escalate_undelivered_autostop_pauses(
                        engine,
                        redis_client,
                        requested_by=_AUTO_STOP_REQUESTED_BY,
                        stuck_after_seconds=_UNDELIVERED_AFTER_SEC,
                        dedup_ttl_seconds=_UNDELIVERED_DEDUP_SEC,
                    )
                except Exception:  # noqa: BLE001
                    logger.debug("undelivered-pause escalation пропущена", exc_info=True)
            try:
                await asyncio.wait_for(stop.wait(), timeout=IDLE_SLEEP_SECONDS)
            except asyncio.TimeoutError:
                pass
            continue

        await process_one_task(
            engine,
            claim.task,
            client=client,
            redis_client=redis_client,
            alert_ctx=alert_ctx,
        )


# ====================== entrypoint ======================


async def main_loop(database_url: str | None = None) -> None:
    db_url = database_url or _get_database_url()
    engine = create_async_engine(db_url, **WORKER_ENGINE_KWARGS)
    redis_client = redis_asyncio.from_url(_get_redis_url(), decode_responses=True)

    # MetaApiClient — eager-init: gRPC channel создаётся без блокировки;
    # реальный fail только при первом ExecuteGraphCall, маршрутизируется в requeue.
    meta_client = _build_meta_client(engine)
    await meta_client.start()

    # CRITICAL-алерт «канал auto-stop мёртв» (#2). Рассылается всем recipients через engine.
    alert_ctx = AutostopAlertContext(engine=engine)

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig_name in ("SIGTERM", "SIGINT"):
        try:
            loop.add_signal_handler(getattr(signal, sig_name), stop.set)
        except (NotImplementedError, RuntimeError):
            pass

    logger.info("meta_api_worker запущен (MetaApiClient ready)")
    try:
        await asyncio.gather(
            task_loop(
                engine,
                stop,
                client=meta_client,
                redis_client=redis_client,
                alert_ctx=alert_ctx,
            ),
            heartbeat_loop(redis_client, stop),
        )
    finally:
        try:
            await meta_client.close()
        except Exception:  # noqa: BLE001
            logger.exception("meta_client.close() упал")
        try:
            await redis_client.aclose()
        except Exception:  # noqa: BLE001
            pass
        await engine.dispose()
        logger.info("meta_api_worker остановлен")
