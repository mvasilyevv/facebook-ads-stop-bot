# -*- coding: utf-8 -*-
"""Пакет persistent-очереди Telegram-алёртов через Redis."""

from core.alerts.queue import AlertQueue
from core.alerts.send import send_telegram_via_queue

__all__ = ["AlertQueue", "send_telegram_via_queue"]
