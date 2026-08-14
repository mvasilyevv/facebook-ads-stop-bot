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
    "frequency_anomaly": "Выгорание аудитории",
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
    "frequency_anomaly": "Выгорание",
}

# Что именно меряет правило — подлежащее для фразы «Цена регистрации 0.41 при
# стопе 0.48». Название правила («Дорогая рега») отвечает на вопрос «что
# случилось», а это — на вопрос «какое число сравнили с порогом».
RULE_METRIC_LABELS: dict[str, str] = {
    "cpc_stop": "Цена клика",
    "cpl_stop": "Цена лида",
    "cpr_stop": "Цена регистрации",
    "regs_no_dep_stop": "Регистраций без депозита",
    "spend_no_dep_range": "Расход без депозита",
    "spend_with_dep_range": "Расход после депозита",
    "early_outbound_ctr_signal": "Доля переходов на PWA",
    "early_lpv_ratio_signal": "Доля открытий PWA после клика",
    "early_cost_per_lpv_signal": "Цена открытия PWA",
    "frequency_anomaly": "Частота показов",
}

# Единица измерения правила. Нужна тексту карточки: деньги требуют
# подтверждённой валюты, spend-диапазоны считаются в процентах от CPA, а
# частота и счётчики единицы не имеют. Без этой карты процент однажды уже
# показывался оператору с валютой рядом.
RULE_METRIC_UNITS: dict[str, str] = {
    "cpc_stop": "money",
    "cpl_stop": "money",
    "cpr_stop": "money",
    "regs_no_dep_stop": "count",
    "spend_no_dep_range": "percent_of_cpa",
    "spend_with_dep_range": "percent_of_cpa",
    "early_outbound_ctr_signal": "percent",
    "early_lpv_ratio_signal": "percent",
    "early_cost_per_lpv_signal": "money",
    "frequency_anomaly": "ratio",
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


def rule_metric_label(code: str) -> str:
    """Возвращает название измеряемой метрики правила («Цена регистрации»)."""
    return RULE_METRIC_LABELS.get(code, rule_label(code))


def rule_metric_unit(code: str) -> str:
    """Единица измерения правила: money / percent_of_cpa / percent / count / ratio."""
    return RULE_METRIC_UNITS.get(code, "ratio")


def alert_state_label(state: str) -> str:
    """Возвращает человекочитаемое название состояния алерта."""
    return ALERT_STATE_LABELS.get(state, state)
