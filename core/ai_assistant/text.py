# -*- coding: utf-8 -*-
"""Канально-нейтральная очистка model text."""

from __future__ import annotations

from html.parser import HTMLParser


class _PlainTextParser(HTMLParser):
    _BREAK_TAGS = frozenset({"br", "div", "li", "p"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        self.parts.append(data)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        _ = attrs
        if tag.lower() == "br":
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in self._BREAK_TAGS:
            self.parts.append("\n")


def html_to_plain_text(value: str) -> str:
    """Удалить HTML-теги и сохранить читаемые переносы строк."""
    parser = _PlainTextParser()
    try:
        parser.feed(value or "")
        parser.close()
    except Exception:  # noqa: BLE001 — битый model output не должен ронять канал
        return value or ""
    lines = [line.strip() for line in "".join(parser.parts).splitlines()]
    return "\n".join(line for line in lines if line).strip()


__all__ = ["html_to_plain_text"]
