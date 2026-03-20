class BrowserHostError(Exception):
    """Базовая ошибка browser host."""


class AdapterConnectionError(BrowserHostError):
    """Ошибка подключения к локальному API anti-detect браузера."""


class AdapterProtocolError(BrowserHostError):
    """Ошибка формата ответа от anti-detect API."""
