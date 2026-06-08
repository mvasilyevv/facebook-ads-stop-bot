# -*- coding: utf-8 -*-
"""Общие хелперы форматирования TG-сообщений — стиль «чистая карточка».

Единый источник правды для всех рендереров (алерты, дайджест, enable-reco,
health, ответы команд). Все сообщения идут с parse_mode=HTML, поэтому хелперы
возвращают готовые HTML-фрагменты и сами экранируют пользовательский ввод.

Принципы стиля:
- Заголовок жирным, одна мысль — одна строка.
- Числа форматируются единообразно (деньги, целые, проценты).
- Табличные данные — в моноширинном <pre>-блоке, выровненные по колонкам
  (Telegram не выравнивает пропорциональный шрифт, моноширинный — единственный
  способ получить ровные колонки).
- Разделитель разрядов — обычный ASCII-пробел: он не схлопывается Telegram'ом
  и сохраняет выравнивание внутри <pre>.

Pure-функции без I/O — легко тестируются и переиспользуются.
"""

from __future__ import annotations

import html
from decimal import Decimal, InvalidOperation
from typing import Any

# Неразрывного пробела избегаем намеренно — внутри <pre> он может рендериться
# другой шириной и ломать выравнивание. Разряды разделяем обычным пробелом.
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


def quote(value: Any) -> str:
    """Блок-цитата (одна строка контекста, напр. название кампании)."""
    return f"<blockquote>{esc(value)}</blockquote>"


def expandable(value: Any) -> str:
    """Раскрывающаяся блок-цитата — прячет детали за тапом."""
    return f"<blockquote expandable>{esc(value)}</blockquote>"


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
# Моноширинные таблицы (выравнивание колонок внутри <pre>)
# ---------------------------------------------------------------------------


def kv_grid(rows: list[list[tuple[str, str]]], *, gap: int = 2) -> str:
    """Сетка «подпись значение» в <pre>, выровненная по колонкам ячеек.

    rows — список строк; каждая строка — список ячеек (подпись, значение).
    Строки могут быть рваными (разное число ячеек). Ширина колонок считается
    по «сырому» тексту, экранирование делается в самом конце — escape-сущности
    (&lt; и т.п.) Telegram рисует одним глифом, поэтому выравнивание не плывёт.
    """
    cells_rows = [r for r in rows if r]
    if not cells_rows:
        return ""

    ncols = max(len(r) for r in cells_rows)
    label_w = [0] * ncols
    value_w = [0] * ncols
    for row in cells_rows:
        for ci, (label, value) in enumerate(row):
            label_w[ci] = max(label_w[ci], len(label))
            value_w[ci] = max(value_w[ci], len(value))

    out_lines: list[str] = []
    for row in cells_rows:
        parts: list[str] = []
        for ci, (label, value) in enumerate(row):
            parts.append(f"{label.ljust(label_w[ci])} {value.ljust(value_w[ci])}")
        out_lines.append(esc((" " * gap).join(parts).rstrip()))
    return "<pre>" + "\n".join(out_lines) + "</pre>"


def table(
    headers: list[str],
    rows: list[list[str]],
    *,
    aligns: list[str] | None = None,
    gap: int = 2,
) -> str:
    """Колоночная таблица с шапкой в <pre> (напр. топ-5 объявлений дайджеста).

    aligns — выравнивание по колонкам: 'l' (влево, дефолт) или 'r' (вправо,
    для чисел). Ширина = max(len(заголовок), max(len(ячейка))). Экранирование —
    на финальной строке, после выравнивания по сырой длине.
    """
    ncols = len(headers)
    aligns = aligns or ["l"] * ncols
    widths = [len(h) for h in headers]
    for row in rows:
        for ci in range(ncols):
            cell = row[ci] if ci < len(row) else ""
            widths[ci] = max(widths[ci], len(cell))

    def _fmt_row(cells: list[str]) -> str:
        parts: list[str] = []
        for ci in range(ncols):
            cell = cells[ci] if ci < len(cells) else ""
            if aligns[ci] == "r":
                parts.append(cell.rjust(widths[ci]))
            else:
                parts.append(cell.ljust(widths[ci]))
        return esc((" " * gap).join(parts).rstrip())

    lines = [_fmt_row(headers)]
    lines.extend(_fmt_row(row) for row in rows)
    return "<pre>" + "\n".join(lines) + "</pre>"


def bullets(items: list[str]) -> list[str]:
    """Список строк-пунктов «• …» (каждый элемент экранируется)."""
    return [f"• {esc(item)}" for item in items]


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
    "esc",
    "expandable",
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
