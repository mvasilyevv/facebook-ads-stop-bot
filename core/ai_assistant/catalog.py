# -*- coding: utf-8 -*-
"""Каталог возможностей AI-ассистента для пользователя (Telegram /tools, /help).

Строится из GLOBAL_REGISTRY, чтобы список не расходился с реальным набором tools.
Курированные русские описания — короче и понятнее, чем technical schema-description
(который пишется для LLM). Тест `test_tg_catalog` проверяет, что покрыты ВСЕ tools
реестра — если добавят новый tool без описания здесь, тест упадёт.
"""

from __future__ import annotations

import core.ai_assistant.tools  # noqa: F401  side-effect: регистрация всех tools в GLOBAL_REGISTRY
from core.ai_assistant.tools.base import RiskLevel
from core.ai_assistant.tools.registry import GLOBAL_REGISTRY

# Человекочитаемые заголовки категорий (по risk_level).
CATEGORY_LABELS: dict[RiskLevel, str] = {
    RiskLevel.READ_ONLY: "📊 Смотреть (ответ сразу)",
    RiskLevel.DRAFT_REQUIRED: "✍️ Действия (через подтверждение ✅ / ❌)",
    RiskLevel.CREATIVE: "🎨 Креатив",
}

# Курированные короткие описания tools для пользователя (RU).
# ВАЖНО: ключи должны покрывать все tools в GLOBAL_REGISTRY (см. test_tg_catalog).
TOOL_DESCRIPTIONS: dict[str, str] = {
    # READ_ONLY
    "get_active_offers": "активные офферы и их стоп-правила",
    "get_recent_alerts": "последние алерты (warning / stop)",
    "get_disable_tasks_status": "статус задач на отключение",
    "get_worker_health": "живы ли воркеры (heartbeat)",
    "find_ads": "поиск объявлений по фильтрам",
    "get_insights": "метрики объявлений/кампаний из Meta",
    "get_offer_performance": "сводка по офферу: spend, лиды, регистрации, депозиты",
    "get_account_health": "состояние рекламного кабинета",
    "get_competitor_patterns": "паттерны конкурентов из Ad Library",
    "get_tracker_stats": "статистика AdSet.pro: клики, реги, депозиты, доход, ROI",
    # DRAFT_REQUIRED
    "request_budget_change": "изменить бюджет адсета",
    "request_bulk_pause": "массовая пауза объявлений по офферу",
    "request_clone_campaign": "клонировать кампанию",
    "request_create_campaign": "создать кампанию",
    # CREATIVE
    "analyze_creative": "разобрать креатив",
    "generate_ad_copy": "сгенерировать рекламный текст",
}

# Примеры запросов на естественном языке для /ask.
EXAMPLES: tuple[str, ...] = (
    "покажи последние стопы за сегодня",
    "сколько потратил оффер GH_CR2 и сколько депозитов",
    "поставь паузу на все объявления KE_CR2",
    "какие воркеры сейчас живы",
    "найди мои объявления со spend больше 1$",
)


def catalog_tool_names() -> set[str]:
    """Имена tools, покрытых каталогом — для теста анти-расхождения с реестром."""
    return set(TOOL_DESCRIPTIONS)


def build_catalog_text() -> str:
    """Рендер каталога возможностей в Markdown для Telegram.

    Группирует tools реестра по risk_level, подставляет курированные описания.
    """
    lines: list[str] = [
        "*Что умеет ассистент* — пиши `/ask <запрос>` обычным языком:\n",
    ]
    for risk in (RiskLevel.READ_ONLY, RiskLevel.DRAFT_REQUIRED, RiskLevel.CREATIVE):
        tools = GLOBAL_REGISTRY.list_by_risk(risk)
        if not tools:
            continue
        lines.append(f"*{CATEGORY_LABELS[risk]}*")
        for tool in sorted(tools, key=lambda t: t.name):
            desc = TOOL_DESCRIPTIONS.get(tool.name, tool.name)
            lines.append(f"• {desc}")
        lines.append("")

    lines.append("*Примеры:*")
    lines.extend(f"  `/ask {ex}`" for ex in EXAMPLES)
    lines.append("\nДействия (пауза/бюджет/создание) приходят черновиком — подтверждаешь ✅ / ❌.")
    return "\n".join(lines)
