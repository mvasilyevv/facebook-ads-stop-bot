# -*- coding: utf-8 -*-
"""Тесты Wave A.3 команд /health, /pause, /resume, /reconnect, /last, /why.

Эти команды удалены из бота — всё переехало в Mini-App.
Тесты проверяют что функции отсутствуют.
"""

import pytest


# Команды /health, /pause, /resume, /reconnect, /last, /why удалены из бота.
def test_wave_a3_commands_removed():
    """Функции _cmd_health, _cmd_pause, _cmd_resume, _cmd_reconnect, _cmd_last, _cmd_why
    должны быть удалены из bot_handler — команды переехали в Mini-App."""
    import core.telegram.bot_handler as bh

    removed_funcs = [
        "_cmd_health",
        "_cmd_pause",
        "_cmd_resume",
        "_cmd_reconnect",
        "_cmd_last",
        "_cmd_why",
    ]
    for fn in removed_funcs:
        assert not hasattr(bh, fn), f"Функция {fn} должна быть удалена"


# Команда /app должна оставаться — она открывает Mini-App.
@pytest.mark.asyncio
async def test_cmd_app_still_exists():
    """_cmd_app должна оставаться в bot_handler — открывает Mini-App."""
    from core.telegram.bot_handler import _cmd_app  # noqa: F401
