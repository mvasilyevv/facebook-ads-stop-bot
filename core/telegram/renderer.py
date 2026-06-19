# -*- coding: utf-8 -*-
"""Форматирование TG-сообщений для алертов observer — стиль «чистая карточка».

Pure-функции без I/O — принимают данные, возвращают (text, inline_keyboard).
parse_mode=HTML (передаётся явно из alert_dispatcher).

Лейаут карточки:
  ⚠️ ПРЕДУПРЕЖДЕНИЕ · KE_CR2          ← заголовок: стадия + оффер
  <пусто>
  CPL $9.56 ▸ порог $3.00 (×3.2)      ← причина(ы) с фактом, порогом и кратностью
  <пусто>
  <pre>выровненный блок метрик</pre>   ← расход / деп / рег / клики / CTR / CPC
  <пусто>
  «название кампании»                  ← контекст в блок-цитате

Без технического ID-хвоста и без дублирования ad_name. Числа и выравнивание —
через core.telegram.format.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.telegram import format as fmt

# Caller (alert_dispatcher) передаёт parse_mode='HTML' явно.
DEFAULT_PARSE_MODE = "HTML"


@dataclass(frozen=True)
class AlertRenderInput:
    """Минимум данных нужный чтобы отрендерить алерт."""

    fb_ad_id: str
    ad_name: str
    campaign_name: str
    adset_name: str
    offer_code: str | None
    stage: str  # 'warning' | 'stop'
    matched_rule_codes: list[str]
    metrics: dict[str, Any]
    open_state_token: str | None  # для callback кнопок


# Заголовок по stage: эмодзи + слово.
_STAGE_HEAD = {
    "warning": ("⚠️", "ПРЕДУПРЕЖДЕНИЕ"),
    "stop": ("🛑", "СТОП"),
}

# code правила → (короткая подпись, единица значения/порога).
_RULE_SHORT: dict[str, tuple[str, str]] = {
    "cpc_stop": ("CPC", "money"),
    "cpl_stop": ("CPL", "money"),
    "cpr_stop": ("CPR", "money"),
    "spend_no_dep_range": ("Расход/CPA", "percent"),
    "spend_with_dep_range": ("Расход/CPA", "percent"),
    "frequency_anomaly": ("Частота", "ratio"),
    "regs_no_dep_stop": ("Рег без деп", "count"),
}

# Fallback-подписи (когда нет _hits с числами — старые события).
_RULE_LABELS = {
    "cpc_stop": "CPC превысил порог",
    "cpl_stop": "CPL превысил порог",
    "cpr_stop": "CPR превысил порог",
    "spend_no_dep_range": "Расход без депозитов в стоп-зоне",
    "spend_with_dep_range": "Расход при депозите превысил порог",
    "frequency_anomaly": "Частота показов: выгорание аудитории",
    "regs_no_dep_stop": "Регистрации есть, депозитов нет",
}


def _format_hit(hit: dict[str, Any]) -> str:
    """Одно сработавшее правило → 'CPL <b>$9.56</b> ▸ порог $3.00 (×3.2)'.

    Значение выделяем жирным (это главный факт), порог и кратность — обычным.
    """
    code = str(hit.get("code") or "")
    short, kind = _RULE_SHORT.get(code, (code or "правило", "raw"))
    value = fmt.unit(hit.get("value"), kind)
    threshold = fmt.unit(hit.get("threshold"), kind)
    mult = fmt.multiplier(hit.get("value"), hit.get("threshold"))
    line = f"{fmt.esc(short)} {fmt.b(value)} ▸ порог {fmt.esc(threshold)}"
    if mult:
        line += f" ({fmt.esc(mult)})"
    return line


def _reason_lines(inp: AlertRenderInput) -> list[str]:
    """Строки причины: из _hits с числами; fallback — текстовые подписи по кодам."""
    hits = [
        h
        for h in (inp.metrics.get("_hits") or [])
        if isinstance(h, dict) and str(h.get("stage")) == inp.stage
    ]
    if hits:
        return [_format_hit(h) for h in hits]
    if inp.matched_rule_codes:
        return [fmt.esc(_RULE_LABELS.get(c, c)) for c in inp.matched_rule_codes]
    return ["сработало стоп-правило"]


def _metrics_grid(m: dict[str, Any]) -> str:
    """Выровненный moноширинный блок ключевых метрик.

    Колонки: Расход / (CPC) — деп / рег — клики / CTR. CPC и CTR показываем
    только при наличии (старые события могли не нести этих полей).
    """
    row1: list[tuple[str, str]] = [("Расход", fmt.money(m.get("spend")))]
    if m.get("cpc") is not None:
        row1.append(("CPC", fmt.money(m.get("cpc"))))

    row2 = [("Деп", fmt.num(m.get("deposits"))), ("Рег", fmt.num(m.get("registrations")))]

    row3: list[tuple[str, str]] = [("Клики", fmt.num(m.get("clicks")))]
    if m.get("ctr") is not None:
        row3.append(("CTR", fmt.pct(m.get("ctr"))))

    return fmt.kv_grid([row1, row2, row3])


def render_alert_text(inp: AlertRenderInput) -> str:
    """Текст TG-карточки для ПРЕДУПРЕЖДЕНИЯ/СТОП (русский, HTML)."""
    emoji, word = _STAGE_HEAD.get(inp.stage, ("ℹ️", "АЛЕРТ"))
    title = inp.offer_code or inp.ad_name or "без названия"

    lines = [f"{emoji} {fmt.b(word)} · {fmt.b(title)}", ""]
    lines.extend(_reason_lines(inp))
    lines.append("")
    lines.append(_metrics_grid(inp.metrics))

    # Контекст: кампания + адсет. Адсет обязателен — названия объявлений
    # дублируются между адсетами, без него непонятно, к какому относится алерт.
    context = _context_block(inp)
    if context:
        lines.append("")
        lines.append(context)

    return "\n".join(line for line in lines if line is not None)


def _context_block(inp: AlertRenderInput) -> str:
    """Блок-цитата с кампанией и адсетом (адсет — маркером вложенности '↳')."""
    ctx: list[str] = []
    if inp.campaign_name:
        ctx.append(inp.campaign_name)
    if inp.adset_name:
        # '↳' — вложенность под кампанией; без кампании показываем просто метку.
        prefix = "↳ адсет: " if inp.campaign_name else "адсет: "
        ctx.append(prefix + inp.adset_name)
    return fmt.quote("\n".join(ctx)) if ctx else ""


def render_inline_keyboard(inp: AlertRenderInput) -> dict | None:
    """Inline-клавиатура с кнопками действий.

    Callback data format: `<action>:<fb_ad_id>:<token>` где action:
    - 'dis'   — отключить

    Snooze убран (решение владельца). Telegram limit на callback_data = 64 bytes.
    """
    token_short = (inp.open_state_token or "")[:8]
    buttons: list[list[dict]] = []

    if inp.stage in ("warning", "stop"):
        buttons.append(
            [
                {
                    "text": "🛑 Отключить",
                    "callback_data": f"dis:{inp.fb_ad_id}:{token_short}",
                },
            ]
        )
    if not buttons:
        return None
    return {"inline_keyboard": buttons}


__all__ = [
    "AlertRenderInput",
    "DEFAULT_PARSE_MODE",
    "render_alert_text",
    "render_inline_keyboard",
]
