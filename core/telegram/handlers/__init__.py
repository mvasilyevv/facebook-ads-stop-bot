# -*- coding: utf-8 -*-
"""Доменные обработчики Telegram-команд и callbacks.

router — handle_update диспатчит обновления по доменам:
- onboarding: /start, /help
- spy: /spy (Ad Library pipeline)
- ask: /ask (AI assistant) + draft callbacks (dr_ok / dr_cancel)
- alerts: callbacks под алертами (dis / snz)
"""

from __future__ import annotations

from core.telegram.handlers.router import handle_update

__all__ = ["handle_update"]
