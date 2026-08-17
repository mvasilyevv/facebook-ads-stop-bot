# -*- coding: utf-8 -*-
"""Контракт нативного канала к рабочему столу Vision."""

from __future__ import annotations

from pydantic import BaseModel


class DesktopNativeChannelResponse(BaseModel):
    """Данные для клиента RustDesk: адрес брокера, ключ, ID стола.

    Пароля здесь нет: этот ответ рендерится в разметку страницы и живёт в ней,
    пока экран открыт. Пароль отдаёт отдельная ручка запуска — по нажатию и
    без следа в HTML. `null` означает «стол ещё не опубликовал значение», а не
    пустую строку.
    """

    available: bool = False
    server: str | None = None
    key: str | None = None
    device_id: str | None = None


class DesktopLaunchLinkResponse(BaseModel):
    """Готовая ссылка запуска клиента RustDesk — со всем, что нужно для входа.

    Отдаётся ТОЛЬКО по явному нажатию владельца и только с `Cache-Control:
    no-store`: ссылка содержит пароль канала, поэтому не должна ни осесть в
    разметке страницы, ни попасть в кэш. Ответ не логируется.
    """

    url: str


__all__ = ["DesktopLaunchLinkResponse", "DesktopNativeChannelResponse"]
