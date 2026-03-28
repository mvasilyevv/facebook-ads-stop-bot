# -*- coding: utf-8 -*-
"""Тесты импорта patchright — убеждаемся что библиотека доступна."""


# Проверяем что patchright импортируется без ошибок
def test_patchright_importable():
    import patchright  # noqa: F401


# Проверяем что async_playwright, Browser, Page доступны из patchright.async_api
def test_patchright_async_api_exports():
    from patchright.async_api import Browser, Page, async_playwright

    assert async_playwright is not None
    assert Browser is not None
    assert Page is not None


# Проверяем что manager.py использует patchright а не playwright
def test_manager_uses_patchright():
    import inspect

    import core.browser.manager as mod

    source = inspect.getsource(mod)
    assert "patchright" in source
    assert "from playwright" not in source
