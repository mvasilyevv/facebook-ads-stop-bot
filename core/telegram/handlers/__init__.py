# -*- coding: utf-8 -*-
"""Active durable webhook handlers.

router — handle_update диспатчит обновления по доменам:
- onboarding: /start, /help
- alerts: recipient-bound opaque incident/action callbacks
"""

from __future__ import annotations

from core.telegram.handlers.router import handle_update

__all__ = ["handle_update"]
