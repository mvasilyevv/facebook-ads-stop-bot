# -*- coding: utf-8 -*-
"""Тесты команды /last — удалена, переехала в Mini-App. Заглушки для CI."""


# Команда /last удалена из бота — всё переехало в Mini-App.
def test_cmd_last_removed():
    """_cmd_last удалена из bot_handler — команда больше не поддерживается в боте."""
    import core.telegram.bot_handler as bh

    assert not hasattr(bh, "_cmd_last"), "Функция _cmd_last должна быть удалена"
