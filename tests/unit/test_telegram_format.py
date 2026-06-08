# -*- coding: utf-8 -*-
"""Unit-тесты core.telegram.format — общие хелперы «чистой карточки»."""

from __future__ import annotations

import re
from decimal import Decimal

from core.telegram import format as f


# Деньги: разделитель разрядов обычным пробелом, два знака, знак $
def test_money_groups_thousands() -> None:
    assert f.money(Decimal("1234.5")) == "$1 234.50"
    assert f.money(0) == "$0.00"
    assert f.money(Decimal("-1500")) == "-$1 500.00"


# Деньги: None и мусор → прочерк, без падения
def test_money_none_and_garbage() -> None:
    assert f.money(None) == "—"
    assert f.money("не число") == "—"


# Целые: группировка разрядов, ноль как «0», None → прочерк
def test_num_formats() -> None:
    assert f.num(1234567) == "1 234 567"
    assert f.num(0) == "0"
    assert f.num(None) == "—"


# Проценты и дробные с точностью
def test_pct_and_dec() -> None:
    assert f.pct(Decimal("1.85")) == "1.85%"
    assert f.pct(Decimal("56"), 0) == "56%"
    assert f.dec(Decimal("0.6789"), 3) == "0.679"
    assert f.dec(None) == "—"


# Кратность превышения порога: значение/порог, скрывается при ×1.0
def test_multiplier() -> None:
    assert f.multiplier(Decimal("9.56"), Decimal("3.00")) == "×3.2"
    assert f.multiplier(Decimal("56"), Decimal("40")) == "×1.4"
    # ниже 1.05 — не показываем (шум)
    assert f.multiplier(Decimal("3.0"), Decimal("3.0")) is None
    # деление на ноль / отсутствие данных
    assert f.multiplier(Decimal("5"), Decimal("0")) is None
    assert f.multiplier(None, Decimal("3")) is None


# esc экранирует HTML-спецсимволы
def test_esc() -> None:
    assert f.esc('<script>&"') == '&lt;script&gt;&amp;"'
    assert f.esc(None) == ""


# Обёртки тегов экранируют контент
def test_tag_wrappers_escape() -> None:
    assert f.b("a<b") == "<b>a&lt;b</b>"
    assert f.code("x>y") == "<code>x&gt;y</code>"
    assert f.quote("CR2 | KE") == "<blockquote>CR2 | KE</blockquote>"


# kv_grid: значения второй колонки выровнены по одному столбцу во всех строках
def test_kv_grid_aligns_columns() -> None:
    out = f.kv_grid(
        [
            [("Расход", "$45.20")],
            [("Деп", "0"), ("Рег", "2")],
            [("Клики", "84"), ("CTR", "1.85%")],
        ]
    )
    assert out.startswith("<pre>") and out.endswith("</pre>")
    body = out[len("<pre>") : -len("</pre>")]
    lines = body.split("\n")
    # «Деп»/«Клики» — первая колонка-подпись: значения 0 и 84 начинаются в одной позиции
    pos_dep = lines[1].index("0")
    pos_clicks = lines[2].index("84")
    assert pos_dep == pos_clicks, f"первая колонка не выровнена: {lines!r}"


# kv_grid экранирует спецсимволы в ячейках, не ломая выравнивание
def test_kv_grid_escapes() -> None:
    out = f.kv_grid([[("Оффер", "X<Y>")]])
    assert "X&lt;Y&gt;" in out
    assert "<Y>" not in out.replace("&lt;Y&gt;", "")


# table: шапка + строки, числовые колонки выровнены вправо
def test_table_renders_header_and_rows() -> None:
    out = f.table(
        ["#", "Оффер", "Spend"],
        [["1", "DRC_CR2", "$142.55"], ["2", "KE_CR2", "$98.10"]],
        aligns=["l", "l", "r"],
    )
    assert "Оффер" in out
    assert "DRC_CR2" in out and "KE_CR2" in out
    assert "$142.55" in out and "$98.10" in out
    # правое выравнивание чисел: '$98.10' дополнено пробелом слева до ширины '$142.55'
    body = out[len("<pre>") : -len("</pre>")]
    rows = body.split("\n")
    assert rows[1].rstrip().endswith("$142.55")
    assert rows[2].rstrip().endswith("$98.10")


# truncate режет длинные строки с '...'
def test_truncate() -> None:
    assert f.truncate("ABCDEFGHIJ", 8) == "ABCDE..."
    assert f.truncate("short", 60) == "short"


# Пустой kv_grid не падает и возвращает пусто
def test_kv_grid_empty() -> None:
    assert f.kv_grid([]) == ""
    assert f.kv_grid([[]]) == ""


# Регресс: ширина колонок считается по «сырой» длине, escape не сдвигает выравнивание
def test_grid_escape_keeps_visual_width() -> None:
    # '<' в сырой строке — 1 символ; после escape '&lt;' длиннее, но это один глиф.
    out = f.kv_grid([[("A", "<")], [("AAA", "1")]])
    # подписи A и AAA → колонка-подпись шириной 3; убеждаемся что escape прошёл
    assert "&lt;" in out
    # сырого '<' (как тега) не осталось
    assert not re.search(r"<(?!/?pre>)", out)
