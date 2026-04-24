"""Единый источник человекочитаемых названий правил и состояний."""

from __future__ import annotations

# Полные названия правил — для Telegram-алертов, детальных экранов
RULE_LABELS: dict[str, str] = {
    "cpc_stop": "Дорогой клик",
    "cpl_stop": "Дорогой лид",
    "cpr_stop": "Дорогая рега",
    "regs_no_dep_stop": "Реги без депозитов",
    "spend_no_dep_range": "Расход без депа",
    "spend_with_dep_range": "Расход с депозитом",
    "early_outbound_ctr_signal": "Мало переходов на PWA",
    "early_lpv_ratio_signal": "Мало открытий PWA после клика",
    "early_cost_per_lpv_signal": "Дорогое открытие PWA",
}

# Короткие названия — для бейджей и таблиц (max ~20 символов)
RULE_LABELS_SHORT: dict[str, str] = {
    "cpc_stop": "Дорогой клик",
    "cpl_stop": "Дорогой лид",
    "cpr_stop": "Дорогая рега",
    "regs_no_dep_stop": "Реги без депов",
    "spend_no_dep_range": "Расход без депа",
    "spend_with_dep_range": "Расход с депозитом",
    "early_outbound_ctr_signal": "Мало переходов",
    "early_lpv_ratio_signal": "Мало открытий PWA",
    "early_cost_per_lpv_signal": "Дорогое открытие",
}

# Человекочитаемые названия состояний алерта
ALERT_STATE_LABELS: dict[str, str] = {
    "NORMAL": "Норма",
    "WARNING_SENT": "Warning",
    "STOP_SENT": "Стоп",
    "CLAIMED": "В работе",
    "DISABLED": "Откл.",
}


def rule_label(code: str, *, short: bool = False) -> str:
    """Возвращает человекочитаемое название правила по коду."""
    source = RULE_LABELS_SHORT if short else RULE_LABELS
    return source.get(code, code)


def alert_state_label(state: str) -> str:
    """Возвращает человекочитаемое название состояния алерта."""
    return ALERT_STATE_LABELS.get(state, state)
