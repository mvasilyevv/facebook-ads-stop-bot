# -*- coding: utf-8 -*-
"""Фасад над `core.telegram.handlers` — сохраняет обратную совместимость импортов.

Реальная реализация разнесена по доменам в `core/telegram/handlers/`:
- onboarding.py    — /start, /help
- spy.py           — /spy
- bulk.py          — /pause /resume
- draft_confirm.py — dr_ok / dr_cancel callbacks
- alerts.py        — dis / ereco callbacks
- router.py        — центральный диспетчер handle_update
"""

from __future__ import annotations

from core.telegram.handlers import handle_update

__all__ = ["handle_update"]
