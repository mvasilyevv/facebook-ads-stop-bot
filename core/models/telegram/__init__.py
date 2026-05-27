# -*- coding: utf-8 -*-
"""Telegram-домен: invite-коды, получатели, ссылки на сообщения."""

from __future__ import annotations

from core.models.telegram.invite import TelegramInvite
from core.models.telegram.message_ref import TelegramMessageRef
from core.models.telegram.recipient import TelegramRecipient

__all__ = [
    "TelegramInvite",
    "TelegramMessageRef",
    "TelegramRecipient",
]
