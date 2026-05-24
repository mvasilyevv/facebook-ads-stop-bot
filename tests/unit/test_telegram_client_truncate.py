# -*- coding: utf-8 -*-
"""Тесты _truncate_message: балансировка HTML-тегов после обрезки до лимита Telegram."""

from __future__ import annotations

import re

import pytest

from core.telegram.client import _TG_HTML_TAGS, _TG_MESSAGE_LIMIT, _truncate_message


# Короткие тексты не модифицируются — обрезка не нужна.
def test_short_text_is_returned_as_is():
    text = "<b>короткое сообщение</b>"
    assert _truncate_message(text) == text


# Текст ровно по лимиту тоже не модифицируется.
def test_text_at_limit_is_returned_as_is():
    text = "a" * _TG_MESSAGE_LIMIT
    assert _truncate_message(text) == text


# Длинный текст без HTML-тегов получает суффикс-маркер обрезки.
def test_plain_long_text_appends_marker():
    text = "a" * (_TG_MESSAGE_LIMIT + 100)
    result = _truncate_message(text)
    assert len(result) <= _TG_MESSAGE_LIMIT
    assert result.endswith("... (сообщение обрезано)")


# _truncate_message не оставляет незакрытый <b> в середине обрезки.
def test_truncate_closes_unclosed_b_tag():
    inner = "x" * (_TG_MESSAGE_LIMIT + 200)
    text = f"<b>{inner}</b>"
    result = _truncate_message(text)
    # Открывающих и закрывающих тегов <b> должно быть поровну
    open_count = len(re.findall(r"<b\b[^>]*>", result, re.IGNORECASE))
    close_count = len(re.findall(r"</b>", result, re.IGNORECASE))
    assert open_count == close_count == 1
    assert "... (сообщение обрезано)" in result


# Несколько вложенных тегов закрываются в правильном порядке (LIFO).
def test_truncate_closes_nested_tags_in_lifo_order():
    inner = "y" * (_TG_MESSAGE_LIMIT + 200)
    text = f"<b><i><code>{inner}</code></i></b>"
    result = _truncate_message(text)
    # Все три тега должны быть сбалансированы
    for tag in ("b", "i", "code"):
        open_count = len(re.findall(rf"<{tag}\b[^>]*>", result, re.IGNORECASE))
        close_count = len(re.findall(rf"</{tag}>", result, re.IGNORECASE))
        assert open_count == close_count == 1, f"тег <{tag}> не сбалансирован"
    # Закрывающие теги должны идти именно в обратном порядке.
    close_b = result.rfind("</b>")
    close_i = result.rfind("</i>")
    close_code = result.rfind("</code>")
    assert close_code < close_i < close_b


# Truncate сохраняет корректный HTML для всех поддерживаемых тегов.
@pytest.mark.parametrize(
    "tag",
    [
        "b",
        "strong",
        "i",
        "em",
        "u",
        "ins",
        "s",
        "strike",
        "del",
        "code",
        "pre",
        "blockquote",
        "tg-spoiler",
        "span",
    ],
)
def test_truncate_balances_all_supported_tags(tag):
    inner = "z" * (_TG_MESSAGE_LIMIT + 100)
    text = f"<{tag}>{inner}</{tag}>"
    result = _truncate_message(text)
    open_count = len(re.findall(rf"<{tag}\b[^>]*>", result, re.IGNORECASE))
    close_count = len(re.findall(rf"</{tag}>", result, re.IGNORECASE))
    assert open_count == close_count == 1


# Уже закрытые теги внутри обрезаемой части не получают дублирующих закрывашек.
def test_already_closed_tags_inside_dont_double_close():
    head = "<b>head</b> "
    long_tail = "x" * (_TG_MESSAGE_LIMIT + 200)
    text = head + long_tail
    result = _truncate_message(text)
    open_count = len(re.findall(r"<b\b[^>]*>", result, re.IGNORECASE))
    close_count = len(re.findall(r"</b>", result, re.IGNORECASE))
    # Ровно одна пара — та, что в начале.
    assert open_count == close_count == 1


# Тег <a href="..."> с атрибутами тоже корректно закрывается.
def test_truncate_closes_anchor_with_attributes():
    inner = "p" * (_TG_MESSAGE_LIMIT + 200)
    text = f'<a href="https://example.com/x">{inner}</a>'
    result = _truncate_message(text)
    open_count = len(re.findall(r"<a\b[^>]*>", result, re.IGNORECASE))
    close_count = len(re.findall(r"</a>", result, re.IGNORECASE))
    assert open_count == close_count == 1


# Обрезка посреди незавершённого открывающего тега не оставляет «обрубок».
def test_truncate_drops_partial_opening_tag_at_boundary():
    # Готовим такой текст, чтобы первый «<» оказался ближе к границе обрезки,
    # чем последний «>». Тогда после обрезки в хвосте остаётся «<co...» без «>».
    prefix = "a" * (_TG_MESSAGE_LIMIT - 30)
    text = prefix + "<code>tail</code>" + "z" * 200
    result = _truncate_message(text)
    # В результирующем тексте перед суффиксом обрезки не должно быть
    # незакрытого «<» (последний «<» обязан иметь соответствующий «>»).
    body = result[: -len("\n\n... (сообщение обрезано)")]
    if "<" in body:
        assert body.rfind("<") < body.rfind(">"), "Остался обрубок открывающего тега"


# Список поддерживаемых тегов покрывает стандартный набор Telegram HTML mode.
def test_supported_tags_include_telegram_html_set():
    required = {"b", "i", "u", "s", "code", "pre", "a", "blockquote", "tg-spoiler"}
    assert required.issubset(set(_TG_HTML_TAGS))
