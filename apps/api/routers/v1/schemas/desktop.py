# -*- coding: utf-8 -*-
"""Контракт нативного канала к рабочему столу Vision."""

from __future__ import annotations

from pydantic import BaseModel


class DesktopNativeChannelResponse(BaseModel):
    """Данные для клиента RustDesk: адрес брокера, ключ, ID стола.

    Пароля здесь нет и не будет: он задаётся владельцем при деплое и в
    операторские поверхности не попадает. `null` означает «стол ещё не
    опубликовал значение», а не пустую строку.
    """

    available: bool = False
    server: str | None = None
    key: str | None = None
    device_id: str | None = None


__all__ = ["DesktopNativeChannelResponse"]
