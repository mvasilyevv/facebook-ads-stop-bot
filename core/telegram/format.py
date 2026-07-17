# -*- coding: utf-8 -*-
"""Общие хелперы Telegram Rich Messages — стиль «операторская карточка».

Единый источник правды для всех рендереров (алерты, дайджест, enable-reco,
health, ответы команд). Результат предназначен для ``sendRichMessage`` с HTML;
клиент умеет безопасно деградировать его до legacy ``sendMessage``.

Принципы стиля:
- Настоящие заголовки h1-h6 и смысловые секции вместо имитации жирным текстом.
- Числа форматируются единообразно (деньги, целые, проценты).
- Табличные данные — нативные ``<table>`` Rich Messages.
- Вторичный контекст можно убирать в ``<details>`` без потери доступности.

Pure-функции без I/O — легко тестируются и переиспользуются.
"""

from __future__ import annotations

import html
from decimal import Decimal, InvalidOperation
from typing import Any

# Разряды разделяем обычным ASCII-пробелом для стабильного отображения.
_GROUP_SEP = " "
_DASH = "—"  # заглушка для отсутствующих значений


# ---------------------------------------------------------------------------
# Экранирование и обёртки тегов
# ---------------------------------------------------------------------------


def esc(value: Any) -> str:
    """HTML-escape любого значения для безопасной вставки в parse_mode=HTML."""
    return html.escape("" if value is None else str(value), quote=False)


def b(value: Any) -> str:
    """Жирный фрагмент (значение экранируется)."""
    return f"<b>{esc(value)}</b>"


def i(value: Any) -> str:
    """Курсивный фрагмент (значение экранируется)."""
    return f"<i>{esc(value)}</i>"


def code(value: Any) -> str:
    """Моноширинный inline-фрагмент (значение экранируется)."""
    return f"<code>{esc(value)}</code>"


def heading(value: Any, level: int = 2) -> str:
    """Настоящий заголовок Rich Message (h1-h6), значение экранируется."""
    safe_level = min(6, max(1, int(level)))
    return f"<h{safe_level}>{esc(value)}</h{safe_level}>"


def footer(value: Any) -> str:
    """Вторичный текст/служебная подпись карточки."""
    return f"<footer>{esc(value)}</footer>"


def divider() -> str:
    """Смысловой разделитель Rich Message."""
    return "<hr/>"


def quote(value: Any) -> str:
    """Блок-цитата (одна строка контекста, напр. название кампании)."""
    return f"<blockquote>{esc(value)}</blockquote>"


def expandable(value: Any) -> str:
    """Сворачиваемый блок деталей с нейтральным заголовком."""
    return details("Подробнее", value)


def details(summary: Any, value: Any, *, open_by_default: bool = False) -> str:
    """Сворачиваемая секция; переносы содержимого сохраняются через ``<br>``."""
    attr = " open" if open_by_default else ""
    body = esc(value).replace("\n", "<br>")
    return f"<details{attr}><summary>{esc(summary)}</summary><p>{body}</p></details>"


# ---------------------------------------------------------------------------
# Числовые форматтеры
# ---------------------------------------------------------------------------


def _to_decimal(value: Any) -> Decimal | None:
    """Безопасно приводит значение к Decimal; None/мусор → None."""
    if value is None:
        return None
    try:
        return value if isinstance(value, Decimal) else Decimal(str(value))
    except (InvalidOperation, ValueError, ArithmeticError, TypeError):
        return None


def _group_thousands(digits: str) -> str:
    """Группирует целую часть по три разряда: '1234567' → '1 234 567'."""
    rev = digits[::-1]
    chunks = [rev[idx : idx + 3] for idx in range(0, len(rev), 3)]
    return _GROUP_SEP.join(chunks)[::-1]


def money(value: Any) -> str:
    """Сумма в USD с разделителем разрядов: 1234.5 → '$1 234.50', None → '—'."""
    dec = _to_decimal(value)
    if dec is None:
        return _DASH
    quantized = dec.quantize(Decimal("0.01"))
    int_part, _, frac_part = f"{quantized:.2f}".partition(".")
    sign = ""
    if int_part.startswith("-"):
        sign = "-"
        int_part = int_part[1:]
    return f"{sign}${_group_thousands(int_part)}.{frac_part}"


def num(value: Any) -> str:
    """Целое с разделителем разрядов: 1234 → '1 234', None → '—'."""
    if value is None:
        return _DASH
    try:
        n = int(value)
    except (TypeError, ValueError):
        return str(value)
    if n == 0:
        return "0"
    return ("-" if n < 0 else "") + _group_thousands(str(abs(n)))


def dec(value: Any, precision: int = 2) -> str:
    """Дробное с фиксированной точностью: 0.6789 → '0.68', None → '—'."""
    d = _to_decimal(value)
    return f"{d:.{precision}f}" if d is not None else _DASH


def pct(value: Any, precision: int = 2) -> str:
    """Процент: 1.85 → '1.85%', None → '—'. precision=0 → '56%'."""
    d = _to_decimal(value)
    return f"{d:.{precision}f}%" if d is not None else _DASH


def unit(value: Any, kind: str) -> str:
    """Значение в нужной единице: money/percent/ratio/count/raw."""
    d = _to_decimal(value)
    if d is None:
        return _DASH
    if kind == "money":
        return money(d)
    if kind == "percent":
        return f"{d:.0f}%"
    if kind == "ratio":
        return f"{d:.2f}"
    if kind == "count":
        return f"{int(d)}"
    return f"{d}"


def multiplier(value: Any, threshold: Any) -> str | None:
    """Во сколько раз значение превысило порог: (9.56, 3.0) → '×3.2'.

    Возвращает None, если данных не хватает или порог невалиден (деление на 0).
    Кратность показываем только когда >= 1.05 — иначе шум (×1.0).
    """
    v = _to_decimal(value)
    t = _to_decimal(threshold)
    if v is None or t is None or t == 0:
        return None
    ratio = abs(v / t)
    if ratio < Decimal("1.05"):
        return None
    return f"×{ratio:.1f}"


# ---------------------------------------------------------------------------
# Нативные Rich Message tables
# ---------------------------------------------------------------------------


def kv_grid(rows: list[list[tuple[str, str]]], *, gap: int = 2) -> str:
    """Нативная таблица пар «метрика / значение».

    ``gap`` оставлен в сигнатуре для обратной совместимости со старыми
    рендерами; в Rich Messages расстояния задаёт клиент Telegram.
    """
    _ = gap
    cells_rows = [r for r in rows if r]
    if not cells_rows:
        return ""

    rendered_rows: list[str] = []
    for row in cells_rows:
        cells = "".join(
            f'<th>{esc(label)}</th><td align="right">{esc(value)}</td>' for label, value in row
        )
        rendered_rows.append(f"<tr>{cells}</tr>")
    return "<table bordered>" + "".join(rendered_rows) + "</table>"


def table(
    headers: list[str],
    rows: list[list[str]],
    *,
    aligns: list[str] | None = None,
    gap: int = 2,
) -> str:
    """Нативная таблица Rich Message с заголовками и выравниванием."""
    _ = gap
    ncols = len(headers)
    aligns = aligns or ["l"] * ncols
    head = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body_rows: list[str] = []
    for row in rows:
        cells: list[str] = []
        for ci in range(ncols):
            cell = row[ci] if ci < len(row) else ""
            align = "right" if aligns[ci] == "r" else "left"
            cells.append(f'<td align="{align}">{esc(cell)}</td>')
        body_rows.append("<tr>" + "".join(cells) + "</tr>")
    return f"<table bordered striped><tr>{head}</tr>" + "".join(body_rows) + "</table>"


def bullets(items: list[str]) -> list[str]:
    """Нативный Rich Message список; возвращает list для совместимости с ``extend``."""
    if not items:
        return []
    return ["<ul>" + "".join(f"<li>{esc(item)}</li>" for item in items) + "</ul>"]


def truncate(value: str, limit: int = 60) -> str:
    """Обрезает длинную строку до limit символов с многоточием '...'."""
    text = value or ""
    if len(text) <= limit:
        return text
    return text[: max(0, limit - 3)] + "..."


__all__ = [
    "b",
    "bullets",
    "code",
    "dec",
    "details",
    "divider",
    "esc",
    "expandable",
    "footer",
    "heading",
    "i",
    "kv_grid",
    "money",
    "multiplier",
    "num",
    "pct",
    "quote",
    "table",
    "truncate",
    "unit",
]
