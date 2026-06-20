# -*- coding: utf-8 -*-
"""Доменные обработчики Telegram-команд и callbacks.

router — handle_update диспатчит обновления по доменам:
- onboarding: /start, /help
- spy: /spy (Ad Library pipeline)
- bulk: /pause /resume + draft_confirm: dr_ok / dr_cancel
- alerts: callbacks под алертами (dis / ereco)
"""

from __future__ import annotations

from core.telegram.handlers.router import handle_update

__all__ = ["handle_update"]
