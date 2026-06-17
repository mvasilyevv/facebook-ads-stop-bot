# -*- coding: utf-8 -*-
"""Pure FSM-логика для ad_alert_state.

Без ORM-зависимостей, без I/O. Принимает текущее состояние + результат evaluator'а,
возвращает план: что записать в БД, что отправить в TG, нужна ли disable-задача.

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
- stop_sent    → claimed       (внешний триггер от disable_reconciler)
- stop_sent    → normal        (восстановление до клика)
- claimed      → disabled      (disable_worker подтвердил)
- disabled     → normal        (enable_worker сбросил)
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


@dataclass(frozen=True)
class FsmTransition:
    """Что должен сделать observer на основе FSM-решения."""

    new_state: AlertState
    new_stage: AlertStage | None
    new_open_token: uuid.UUID | None

    # Нужно ли отправить алерт в TG (новый WARNING или STOP)
    emit_alert: bool = False
    alert_stage: AlertStage | None = None  # warning / stop — для рендерера
    alert_rule_codes: tuple[str, ...] = field(default_factory=tuple)

    # Нужно ли создать disable-task (auto-stop)
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
      callback'и на старой WARNING-карточке (`dis:<fb>:<token>`) остаются валидными.
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
            # Во время активного снуза _suppress_emit обнулит create_disable_task
            # (юзер просил не трогать); после истечения снуза recovery сработает.
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

    Реактивация ВНЕ бота (вручную в Ads Manager или autostart bulk-activate) не проходит
    через enable-путь (reset_after_enable_succeeded), поэтому FSM остаётся `disabled` —
    и decide() для disabled+STOP ничего не делает. Убыточный реактивированный ад крутится
    без стопа (H3). Обнаружив ACTIVE delivery у disabled-ада, observer сбрасывает FSM в
    normal, и повторный STOP срабатывает заново.
    """
    if current_state != "disabled":
        return False
    return (delivery_status or "").strip().upper() == "ACTIVE"


def reset_after_disable_succeeded(current_state: AlertState) -> AlertState:
    """Вызывается из disable_worker'а после успешного клика.

    Любое состояние перед disable → 'disabled'.
    """
    return "disabled"


def reset_after_enable_succeeded(current_state: AlertState) -> AlertState:
    """Вызывается из enable_worker'а после успешного клика.

    Любое состояние → 'normal' (новая жизнь объявления).
    """
    return "normal"


__all__ = [
    "AlertStage",
    "AlertState",
    "FsmInput",
    "FsmTransition",
    "decide",
    "reset_after_disable_succeeded",
    "reset_after_enable_succeeded",
    "should_reopen_disabled",
]
