# -*- coding: utf-8 -*-
"""Pure FSM-логика для ad_alert_state.

Без ORM-зависимостей, без I/O. Принимает текущее состояние + результат evaluator'а,
возвращает план: что записать в БД, какое notification event создать и нужна ли
pause-команда.

Состояния:
- normal              — норма, не уведомляли
- warning_sent        — отправлен WARNING-алерт, ждём либо рестора, либо STOP
- stop_sent           — отправлен STOP-алерт, ждём пользователя или авто-disable
- claimed             — пользователь нажал «Отключить», задача в очереди
- disabled            — реально выключено (либо вручную, либо ботом)

Переходы:
- normal       → warning_sent  (новый WARNING)
- normal       → stop_sent     (сразу STOP без WARNING — fast-stop правило)
- warning_sent → warning_sent  (всё ещё WARNING — НЕ дублируем алерт)
- warning_sent → stop_sent     (эскалация)
- warning_sent → normal        (восстановление)
- stop_sent    → claimed       (pause-команда принята CommandService)
- stop_sent    → normal        (восстановление до клика)
- claimed      → disabled      (Meta worker подтвердил pause)
- disabled     → normal        (Meta worker подтвердил activate)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Literal

# Допустимые состояния — должны совпадать с тем что хранится в ad_alert_state.alert_state
AlertState = Literal["normal", "warning_sent", "stop_sent", "claimed", "disabled"]

AlertStage = Literal["warning", "stop"]


@dataclass(frozen=True)
class FsmInput:
    """Вход FSM — что мы знаем про ад прямо сейчас."""

    current_state: AlertState
    current_stage: AlertStage | None  # последний известный этап (для эскалации)
    current_open_token: uuid.UUID | None  # FSM token действующего инцидента

    # Результат evaluator'а на этом цикле:
    warning_rule_codes: tuple[str, ...]
    stop_rule_codes: tuple[str, ...]

    # MID-1: строка принадлежит zero-scan'у начала новых суток кабинета (все метрики
    # обнулены Meta на границе дня). Первая строка нового дня всегда без хитов —
    # НЕ признак восстановления, а артефакт сброса счётчиков. При True FSM не
    # деэскалирует активный инцидент (warning_sent/stop_sent) по нулевой строке.
    is_cabinet_reset: bool = False


@dataclass(frozen=True)
class FsmTransition:
    """Что должен сделать observer на основе FSM-решения."""

    new_state: AlertState
    new_stage: AlertStage | None
    new_open_token: uuid.UUID | None

    # Нужно ли создать notification event (новый WARNING или STOP)
    emit_alert: bool = False
    alert_stage: AlertStage | None = None  # warning / stop — для рендерера
    alert_rule_codes: tuple[str, ...] = field(default_factory=tuple)

    # Нужно ли создать pause-задачу auto-stop
    create_disable_task: bool = False

    # Описание перехода для логов
    transition_reason: str = ""


def _has_warnings(inp: FsmInput) -> bool:
    return bool(inp.warning_rule_codes)


def _has_stops(inp: FsmInput) -> bool:
    return bool(inp.stop_rule_codes)


def decide(inp: FsmInput) -> FsmTransition:
    """Главная функция FSM. Решает что делать на основе текущего state + правил.

    Pure: одинаковый вход → одинаковый выход. Никакого I/O, можно тестировать
    в любом количестве сценариев.

    Контракт open_state_token:
    - normal → warning_sent / stop_sent — новый incident, генерируем uuid4().
    - warning_sent → stop_sent — эскалация ТОГО ЖЕ incident'а: token сохраняется,
      notification plane обновляет ту же incident-карточку.
    - повторы внутри одного состояния (stop_sent → stop_sent и пр.) — token сохраняется.
    - восстановление в normal — token обнуляется (incident закрыт).
    """
    cur = inp.current_state

    # --- STOP всегда побеждает: если есть стоп-правила, мы эскалируем ---
    if _has_stops(inp):
        if cur == "warning_sent":
            # Эскалация того же incident'а: сохраняем существующий token,
            # чтобы старые WARNING inline-кнопки остались валидны.
            escalation_token = inp.current_open_token or uuid.uuid4()
            return FsmTransition(
                new_state="stop_sent",
                new_stage="stop",
                new_open_token=escalation_token,
                emit_alert=True,
                alert_stage="stop",
                alert_rule_codes=inp.stop_rule_codes,
                create_disable_task=True,
                transition_reason="warning_sent → stop_sent (эскалация, token сохранён)",
            )
        if cur == "normal":
            return FsmTransition(
                new_state="stop_sent",
                new_stage="stop",
                new_open_token=uuid.uuid4(),
                emit_alert=True,
                alert_stage="stop",
                alert_rule_codes=inp.stop_rule_codes,
                create_disable_task=True,
                transition_reason="normal → stop_sent (новый STOP-инцидент)",
            )
        if cur == "stop_sent":
            # Уже на STOP — алерт не дублируем, НО включаем recovery pause-задачи.
            # Если задача не была создана (снуз подавил create_disable_task на исходном
            # переходе, либо краш между коммитом FSM 'stop_sent' и созданием outbox) —
            # создаём её на следующем скане. idempotency_key привязан к open_token
            # инцидента → если задача уже есть/исполнена, повтор даёт UNIQUE conflict
            # → no-op (одна задача на инцидент). Закрывает money-залип в stop_sent.
            # MID-2: снуз (_suppress_emit) больше НЕ обнуляет create_disable_task —
            # авто-стоп работает и под снузом, снуз глушит только TG-алерт.
            return FsmTransition(
                new_state="stop_sent",
                new_stage="stop",
                new_open_token=inp.current_open_token,
                emit_alert=False,
                create_disable_task=True,
                transition_reason="stop_sent → stop_sent (STOP активен, recovery pause-задачи)",
            )
        if cur in ("claimed", "disabled"):
            # Уже в процессе обработки или выключено — ничего не делаем
            return FsmTransition(
                new_state=cur,
                new_stage=inp.current_stage,
                new_open_token=inp.current_open_token,
                emit_alert=False,
                transition_reason=f"{cur} → {cur} (STOP остаётся, ждём финализации)",
            )

    # --- WARNING без STOP ---
    if _has_warnings(inp):
        if cur == "normal":
            return FsmTransition(
                new_state="warning_sent",
                new_stage="warning",
                new_open_token=uuid.uuid4(),
                emit_alert=True,
                alert_stage="warning",
                alert_rule_codes=inp.warning_rule_codes,
                transition_reason="normal → warning_sent",
            )
        if cur == "warning_sent":
            # Всё ещё в WARNING — не дублируем (idempotent guard)
            return FsmTransition(
                new_state="warning_sent",
                new_stage="warning",
                new_open_token=inp.current_open_token,
                emit_alert=False,
                transition_reason="warning_sent → warning_sent (повтор WARNING — не дублируем)",
            )
        if cur == "stop_sent":
            # Странно — был STOP, теперь только WARNING. Деэскалация до warning_sent.
            return FsmTransition(
                new_state="warning_sent",
                new_stage="warning",
                new_open_token=inp.current_open_token,
                emit_alert=False,
                transition_reason="stop_sent → warning_sent (деэскалация без emit)",
            )
        # claimed/disabled — не трогаем
        return FsmTransition(
            new_state=cur,
            new_stage=inp.current_stage,
            new_open_token=inp.current_open_token,
            transition_reason=f"{cur} → {cur} (WARNING игнорируется в финальных состояниях)",
        )

    # --- Нет ни WARNING ни STOP → потенциальное восстановление ---
    if cur in ("warning_sent", "stop_sent"):
        # MID-1: на границе кабинетных суток Meta обнуляет метрики → первая строка
        # нового дня всегда без хитов. Это НЕ восстановление, а reset счётчиков.
        # Деэскалация активного инцидента по такой строке потеряла бы stop_sent
        # (человек решил бы, что ад закрыт, хотя он лишь «обнулился»). Удерживаем
        # текущее состояние без emit; реальная деэскалация (не все нули среди дня)
        # сюда не попадает — там is_cabinet_reset=False.
        if inp.is_cabinet_reset:
            return FsmTransition(
                new_state=cur,
                new_stage=inp.current_stage,
                new_open_token=inp.current_open_token,
                emit_alert=False,
                transition_reason=(
                    f"{cur} → {cur} (zero-scan нового кабинетного дня — не деэскалируем)"
                ),
            )
        return FsmTransition(
            new_state="normal",
            new_stage=None,
            new_open_token=None,
            emit_alert=False,
            transition_reason=f"{cur} → normal (правила больше не сработали)",
        )

    # claimed / disabled / normal — оставляем как есть
    return FsmTransition(
        new_state=cur,
        new_stage=inp.current_stage,
        new_open_token=inp.current_open_token,
        transition_reason=f"{cur} → {cur} (no change)",
    )


def should_reopen_disabled(current_state: AlertState, delivery_status: str | None) -> bool:
    """True если ад в `disabled`, но снова ACTIVE в кабинете → нужен reopen в `normal`.

    Реактивация вне FB Agent (вручную в Ads Manager) не проходит
    через подтверждённую activate-команду, поэтому FSM остаётся `disabled` —
    и decide() для disabled+STOP ничего не делает. Убыточный реактивированный ад крутится
    без стопа (H3). Обнаружив ACTIVE delivery у disabled-ада, observer сбрасывает FSM в
    normal, и повторный STOP срабатывает заново.
    """
    if current_state != "disabled":
        return False
    return (delivery_status or "").strip().upper() == "ACTIVE"


def should_sync_disabled(current_state: AlertState, delivery_status: str | None) -> bool:
    """True если ад завис в инциденте (warning_sent/stop_sent), а в Meta уже OFF → disabled.

    Зеркало should_reopen_disabled. Терминальный `disabled` штатно ставит только
    fsm_sync после УСПЕШНОЙ pause-мутации. Если наша pause упала (или ад выключили
    вручную/выше по иерархии), FSM застревает в stop_sent, хотя ад фактически OFF —
    рассинхрон (косметика, но вечный: метрик у OFF-ада нет → переходов нет). Обнаружив
    OFF у инцидентного ада, observer сам приводит FSM к disabled.

    Строго `OFF` (paused/archived/deleted — реально выключен). НЕ трогаем ACTIVE
    (крутит — stop остаётся в силе, pause ретраится), модерацию (IN_REVIEW/
    NOT_DELIVERING/PROCESSING — ад может сам вернуться в ACTIVE) и терминальные/normal.
    Writer добавляет time-guard (cooldown), чтобы не опередить штатный fsm_sync.
    """
    if current_state not in ("warning_sent", "stop_sent"):
        return False
    return (delivery_status or "").strip().upper() == "OFF"


__all__ = [
    "AlertStage",
    "AlertState",
    "FsmInput",
    "FsmTransition",
    "decide",
    "should_reopen_disabled",
    "should_sync_disabled",
]
