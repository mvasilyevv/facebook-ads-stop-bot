# -*- coding: utf-8 -*-
"""Причина исхода задачи словами оператора — записанная, а не выведенная из состояния.

Инвариант: причина, которую видит оператор, приходит из записи о завершении
задачи (``task_queue.result``). Функция от состояния даёт всем отказам один
текст — 20.08.2026 пять заливов, упавших по пяти разным причинам (отключённый
кабинет, потерянный контекст страницы, недоступный браузер, исчерпанный
дедлайн), читались в очереди действий одинаково.

Второй инвариант: отсутствие записанной причины — ``None``. Ни пустая строка,
ни бодрая константа: «неизвестно» остаётся неизвестным.

Третий инвариант: наружу едет только закрытый словарь плюс санитизированный
текст Meta. Машинный код причины, traceback, UUID, ``fbtrace_id`` и секреты в
операторский текст не попадают — см. ``sanitize_operator_reason``.
"""

from __future__ import annotations

import re
from typing import Any, Mapping

from core.meta_api.errors import BROWSER_OPERATION_REJECTION_REASONS
from core.safe_diagnostics import redact_sensitive_text

# Длина одной строки в ленте действий. Причина длиннее не помогает оператору и
# начинает работать как канал утечки: чем длиннее свободный текст, тем выше шанс
# протащить в него внутренности.
OPERATOR_REASON_MAX_LEN = 240

# Признаки внутренностей Python. Такой текст в поле причины не редактируется, а
# отбрасывается целиком: показать оператору обрезанный traceback хуже, чем
# честно сказать «причина не записана».
_INTERNALS_RE = re.compile(
    r"Traceback \(most recent call last\)|File \"[^\"]+\", line \d+|line \d+, in ",
)

# Шаг залива → как он называется у оператора. Ключи — закрытый словарь стадий
# из ``core/campaign_builder/execute.py``; незнакомый шаг просто не называется.
CAMPAIGN_STEP_LABELS: dict[str, str] = {
    "validate": "проверка конфигурации",
    "uniquifying": "подготовка уникальных имён",
    "uploading": "загрузка креативов",
    "creating": "создание объектов кампании",
}

# Машинный код причины финализации → та же причина словами оператора. Словарь
# закрытый: незнакомый код НЕ пробрасывается как есть — внутренний код в
# карточке оператора запрещён каноном.
CAMPAIGN_REASON_CAUSES: dict[str, str] = {
    "absolute_deadline_exceeded": ("Истёк отведённый заливу срок уже после обращения к кабинету"),
    "absolute_deadline_exceeded_before_external_call": (
        "Истёк отведённый заливу срок до первого запроса в Meta"
    ),
    "ack_lost_nothing_confirmed": (
        "Ответ Meta потерян после отправки кампании, подтверждённых объектов нет"
    ),
    "cancel_requested": "Залив остановлен по запросу отмены",
    "creator_dependencies_unavailable": "Сервисы залива недоступны",
    "deadline_exceeded": "Истёк отведённый заливу срок",
    "external_result_ambiguous": (
        "Связь с кабинетом оборвалась после отправки запроса, итог неизвестен"
    ),
    "invalid_config": "Конфигурация залива не прошла проверку",
    "partial_confirmed": ("Часть объектов уже создана в кабинете, остальные создать не удалось"),
    "permanent_pre_external_failure": "Meta отказала до создания объектов",
    "pre_external_attempts_exhausted": ("Попытки закончились до первого запроса в Meta"),
    "preexisting_external_boundary": ("Предыдущая попытка этого залива уже обращалась к кабинету"),
    "preexisting_in_progress_or_created_objects": (
        "Предыдущая попытка этого залива уже создала объекты или ещё идёт"
    ),
    "run_cancelled_before_external_call": "Залив отменён до первого запроса в Meta",
    "run_not_found": "Запуск залива не найден",
    "unexpected_worker_crash": "Воркер залива остановился неожиданно",
}

# ``run_already_<status>`` строится из статуса запуска, поэтому в словаре его
# нет: статус — внутреннее значение, наружу едет одна причина на всю семью.
_RUN_ALREADY_PREFIX = "run_already_"
_RUN_ALREADY_CAUSE = "Залив уже в работе или завершён, повторный запуск не начат"


def sanitize_operator_reason(value: Any) -> str | None:
    """Причина, пригодная для показа оператору, — или ``None``.

    Порядок жёсткий: сначала схлопывание пробелов, затем отсев внутренностей,
    затем редактирование секретов и только потом ограничение длины. Обрезать
    первым нельзя: обрезанный ``access_token=`` перестаёт совпадать с шаблоном
    и уезжает наружу хвостом.
    """
    if not isinstance(value, str):
        return None
    collapsed = " ".join(value.split())
    if not collapsed or _INTERNALS_RE.search(collapsed):
        return None
    redacted = " ".join(redact_sensitive_text(collapsed).split())
    if not redacted:
        return None
    if len(redacted) > OPERATOR_REASON_MAX_LEN:
        redacted = redacted[: OPERATOR_REASON_MAX_LEN - 1].rstrip() + "…"
    return redacted


def campaign_operator_reason(
    *,
    reason_code: str | None = None,
    failed_step: str | None = None,
    rejection_reason_code: str | None = None,
    meta_message: str | None = None,
) -> str | None:
    """Собрать причину отказа залива: шаг, причина и ответ Meta.

    ``meta_message`` — уже санитизированный текст отказа Meta
    (``MetaApiError.meta_message``). Именно он отличает «Отключенные аккаунты не
    могут создавать или редактировать рекламу» от кода вида «100/1885316».

    Шаг сам по себе причиной не является: без известной причины, отказа браузера
    и ответа Meta возвращается ``None``, а не строка «Шаг: …».
    """
    code = str(reason_code or "").strip()
    cause = CAMPAIGN_REASON_CAUSES.get(code)
    if cause is None and code.startswith(_RUN_ALREADY_PREFIX):
        cause = _RUN_ALREADY_CAUSE
    rejection = BROWSER_OPERATION_REJECTION_REASONS.get(str(rejection_reason_code or "").strip())
    meta = sanitize_operator_reason(meta_message)
    if not (cause or rejection or meta):
        return None
    parts: list[str] = []
    step = CAMPAIGN_STEP_LABELS.get(str(failed_step or "").strip())
    if step:
        parts.append(f"Шаг: {step}")
    if cause:
        parts.append(cause)
    if rejection:
        parts.append(f"Браузер отказал до отправки: {rejection}")
    if meta:
        parts.append(f"Ответ Meta: {meta.rstrip('.')}")
    return sanitize_operator_reason(". ".join(parts) + ".")


def operator_reason_from_result(result: Mapping[str, Any] | None) -> str | None:
    """Прочитать записанную причину из результата задачи.

    Читается ровно один ключ. Соседние поля результата (``reason``, ``error``,
    ``diagnostics``) несут машинные коды и внутреннюю диагностику — путь к
    оператору у них закрыт, иначе поле причины станет каналом утечки.
    """
    if not isinstance(result, Mapping):
        return None
    return sanitize_operator_reason(result.get("operator_reason"))


__all__ = [
    "CAMPAIGN_REASON_CAUSES",
    "CAMPAIGN_STEP_LABELS",
    "OPERATOR_REASON_MAX_LEN",
    "campaign_operator_reason",
    "operator_reason_from_result",
    "sanitize_operator_reason",
]
