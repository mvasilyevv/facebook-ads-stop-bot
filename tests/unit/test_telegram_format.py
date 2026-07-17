# -*- coding: utf-8 -*-
"""Unit-тесты core.telegram.format — общие хелперы «чистой карточки»."""

from __future__ import annotations

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


# kv_grid: нативная таблица с label/value cells.
def test_kv_grid_renders_native_table() -> None:
    out = f.kv_grid(
        [
            [("Расход", "$45.20")],
            [("Деп", "0"), ("Рег", "2")],
            [("Клики", "84"), ("CTR", "1.85%")],
        ]
    )
    assert out.startswith("<table bordered>") and out.endswith("</table>")
    assert out.count("<tr>") == 3
    assert '<td align="right">$45.20</td>' in out
    assert "<th>Деп</th>" in out and "<th>CTR</th>" in out


# kv_grid экранирует спецсимволы в ячейках, не ломая выравнивание
def test_kv_grid_escapes() -> None:
    out = f.kv_grid([[("Оффер", "X<Y>")]])
    assert "X&lt;Y&gt;" in out
    assert "<Y>" not in out.replace("&lt;Y&gt;", "")


# table: нативная шапка + строки, числовые колонки помечены right-align.
def test_table_renders_header_and_rows() -> None:
    out = f.table(
        ["#", "Оффер", "Spend"],
        [["1", "DRC_CR2", "$142.55"], ["2", "KE_CR2", "$98.10"]],
        aligns=["l", "l", "r"],
    )
    assert "Оффер" in out
    assert "DRC_CR2" in out and "KE_CR2" in out
    assert "$142.55" in out and "$98.10" in out
    assert out.startswith("<table bordered striped>")
    assert "<th>Spend</th>" in out
    assert '<td align="right">$142.55</td>' in out
    assert '<td align="right">$98.10</td>' in out


# truncate режет длинные строки с '...'
def test_truncate() -> None:
    assert f.truncate("ABCDEFGHIJ", 8) == "ABCDE..."
    assert f.truncate("short", 60) == "short"


# Пустой kv_grid не падает и возвращает пусто
def test_kv_grid_empty() -> None:
    assert f.kv_grid([]) == ""
    assert f.kv_grid([[]]) == ""


# Rich table сохраняет escape внутри cells.
def test_grid_escape_stays_inside_cell() -> None:
    out = f.kv_grid([[("A", "<")], [("AAA", "1")]])
    assert "&lt;" in out
    assert '<td align="right">&lt;</td>' in out


def test_rich_structure_helpers_escape_content() -> None:
    assert f.heading("A<B", 1) == "<h1>A&lt;B</h1>"
    assert f.footer("окно & дата") == "<footer>окно &amp; дата</footer>"
    assert f.divider() == "<hr/>"
    assert f.details("Контекст", "A&B\nАдсет <x>", open_by_default=True) == (
        "<details open><summary>Контекст</summary><p>A&amp;B<br>Адсет &lt;x&gt;</p></details>"
    )
    assert f.bullets(["CPL > $3", "A&B"]) == ["<ul><li>CPL &gt; $3</li><li>A&amp;B</li></ul>"]
