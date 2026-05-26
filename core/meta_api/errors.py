"""Каталог ошибок Marketing API для FB Stop Bot.

Иерархия исключений:
    MetaApiError (базовое)
    ├── TokenInvalidatedError  — code 190, токен невалиден
    ├── RateLimitedError       — codes 4, 17, 32, 613, 80004
    ├── PermissionDeniedError  — codes 10, 200
    ├── InvalidRequestError    — codes 1, 100
    ├── TransientError         — code 2, сетевые ошибки
    ├── SessionDeadError       — наши кастомные codes -1, -2, -3
    ├── ActionBlockedError     — code 368
    └── DeprecatedApiError     — code 2635

Использование:
    from core.meta_api.errors import classify_meta_api_error, is_retryable

    try:
        result = await client.execute_graph_call(...)
    except MetaApiError as exc:
        if is_retryable(exc):
            await asyncio.sleep(recommended_retry_delay(exc, attempt=1))
            ...
        elif is_token_problem(exc):
            await session_manager.reboot_vision_session()
        else:
            raise
"""

from __future__ import annotations

__all__ = [
    "MetaApiError",
    "TokenInvalidatedError",
    "RateLimitedError",
    "PermissionDeniedError",
    "InvalidRequestError",
    "TransientError",
    "SessionDeadError",
    "ActionBlockedError",
    "DeprecatedApiError",
    "classify_meta_api_error",
    "is_retryable",
    "is_token_problem",
    "recommended_retry_delay",
]


# ─────────────────────────────────────────────────────────────────────────────
# Базовый класс
# ─────────────────────────────────────────────────────────────────────────────


class MetaApiError(Exception):
    """Базовая ошибка Marketing API.

    Содержит code/subcode/type/fbtrace_id для диагностики и логирования.
    Не импортировать MetaApiError из clients/ — тот класс наследует RuntimeError
    и предназначен для gRPC-слоя; этот — для доменной логики core/.
    """

    def __init__(
        self,
        message: str,
        *,
        code: int = 0,
        subcode: int = 0,
        type_: str = "",
        fbtrace_id: str = "",
        status_code: int = 0,
    ) -> None:
        self.code = code
        self.subcode = subcode
        self.type = type_
        self.fbtrace_id = fbtrace_id
        self.status_code = status_code
        super().__init__(message)

    def __repr__(self) -> str:
        return (
            f"{self.__class__.__name__}("
            f"code={self.code}, subcode={self.subcode}, "
            f"type={self.type!r}, message={str(self)!r}"
            f")"
        )


# ─────────────────────────────────────────────────────────────────────────────
# Специализированные подклассы
# ─────────────────────────────────────────────────────────────────────────────


class TokenInvalidatedError(MetaApiError):
    """Токен инвалидирован (code 190). Нужна перезагрузка Vision-сессии.

    subcode 460=пароль сменён, 463=токен истёк, 464=не подтверждён, 467=невалидный.
    """


class RateLimitedError(MetaApiError):
    """Rate limit Meta (codes 4, 17, 32, 613, 80004). Retryable с backoff."""


class PermissionDeniedError(MetaApiError):
    """Нет прав на операцию (codes 10, 200). Не retryable."""


class InvalidRequestError(MetaApiError):
    """Некорректный запрос (codes 1, 100, 506). Не retryable."""


class TransientError(MetaApiError):
    """Временная ошибка Meta (code 2). Retryable."""


class SessionDeadError(MetaApiError):
    """Vision-сессия мертва (наши коды -1, -2, -3). Требуется bootstrap заново.

    -1 — токен не найден в page source, -2 — network/timeout, -3 — page.evaluate упал.
    """


class ActionBlockedError(MetaApiError):
    """Anti-spam блокировка Meta (code 368). Не retryable, нужно ручное вмешательство."""


class DeprecatedApiError(MetaApiError):
    """Устаревшая версия Ads API (code 2635). Обновить META_API_VERSION."""


# ─────────────────────────────────────────────────────────────────────────────
# Маппинг code → класс исключения
# ─────────────────────────────────────────────────────────────────────────────

# Коды, для которых достаточно одного code без subcode
_CODE_TO_CLASS: dict[int, type[MetaApiError]] = {
    # Токен
    190: TokenInvalidatedError,
    # Rate limit
    4: RateLimitedError,
    17: RateLimitedError,
    32: RateLimitedError,
    613: RateLimitedError,
    80004: RateLimitedError,
    # Права
    10: PermissionDeniedError,
    200: PermissionDeniedError,
    # Битый запрос
    1: InvalidRequestError,
    100: InvalidRequestError,
    # Временная ошибка
    2: TransientError,
    # Наши кастомные (browser-agent)
    -1: SessionDeadError,
    -2: SessionDeadError,
    -3: SessionDeadError,
    # Блокировка действия
    368: ActionBlockedError,
    # Устаревший API
    2635: DeprecatedApiError,
    # Дублирующий пост (близко к InvalidRequest, но отдельный смысл)
    506: InvalidRequestError,
}


# ─────────────────────────────────────────────────────────────────────────────
# Factory
# ─────────────────────────────────────────────────────────────────────────────


def classify_meta_api_error(
    *,
    code: int,
    subcode: int = 0,
    type_: str = "",
    message: str = "",
    fbtrace_id: str = "",
    status_code: int = 0,
) -> MetaApiError:
    """Создать конкретный экземпляр исключения по code/subcode от Meta.

    Сообщение формируется на русском с включением кода для облегчения диагностики.
    Если code неизвестен — возвращает базовый MetaApiError.

    Args:
        code:        Meta error code (или наш кастомный для browser-agent ошибок)
        subcode:     Meta error_subcode (опционально)
        type_:       Meta error type (OAuthException и т.п.)
        message:     Исходное сообщение от Meta (используется как дополнение)
        fbtrace_id:  Идентификатор трассировки Meta для саппорта
        status_code: HTTP-статус ответа (обычно 400 или 200)

    Returns:
        Экземпляр конкретного подкласса MetaApiError (или самого MetaApiError).
    """
    cls = _CODE_TO_CLASS.get(code, MetaApiError)

    # Формируем информативное русскоязычное сообщение
    russian_message = _build_russian_message(cls, code=code, subcode=subcode, original=message)

    return cls(
        russian_message,
        code=code,
        subcode=subcode,
        type_=type_,
        fbtrace_id=fbtrace_id,
        status_code=status_code,
    )


def _build_russian_message(
    cls: type[MetaApiError],
    *,
    code: int,
    subcode: int,
    original: str,
) -> str:
    """Сформировать русскоязычное сообщение по классу и коду."""
    suffix = f" (code={code}" + (f", subcode={subcode}" if subcode else "") + ")"
    if original:
        suffix += f": {original}"

    if cls is TokenInvalidatedError:
        reasons = {
            460: "пользователь сменил пароль",
            463: "токен истёк по TTL",
            464: "пользователь не подтверждён",
            467: "невалидный токен",
        }
        detail = reasons.get(subcode, "токен инвалидирован")
        return f"Токен Facebook инвалидирован — {detail}{suffix}"

    if cls is RateLimitedError:
        limits = {
            4: "превышен лимит запросов приложения",
            17: "превышен лимит запросов пользователя",
            32: "превышен лимит запросов страницы",
            613: "превышен кастомный rate limit",
            80004: "превышен лимит запросов рекламного кабинета",
        }
        detail = limits.get(code, "rate limit")
        return f"Marketing API: {detail}{suffix}"

    if cls is PermissionDeniedError:
        return f"Недостаточно прав для операции{suffix}"

    if cls is InvalidRequestError:
        return f"Некорректный запрос к Marketing API{suffix}"

    if cls is TransientError:
        return f"Временная ошибка Meta, повторная попытка возможна{suffix}"

    if cls is SessionDeadError:
        reasons = {
            -1: "EAA-токен не найден в page source",
            -2: "сетевой сбой или timeout при fetch()",
            -3: "page.evaluate() завершился с ошибкой (браузер упал?)",
        }
        detail = reasons.get(code, "Vision-сессия недоступна")
        return f"Vision-сессия мертва — {detail}{suffix}"

    if cls is ActionBlockedError:
        return f"Meta заблокировала действие (anti-spam){suffix}"

    if cls is DeprecatedApiError:
        return f"Используется устаревшая версия Ads API{suffix}"

    # Базовый класс — неизвестный код
    return f"Неизвестная ошибка Marketing API{suffix}"


# ─────────────────────────────────────────────────────────────────────────────
# Утилиты для retry-логики
# ─────────────────────────────────────────────────────────────────────────────


def is_retryable(error: MetaApiError) -> bool:
    """True, если ошибку имеет смысл повторить с задержкой.

    Retryable: TransientError (временные сбои) и RateLimitedError (rate limit).
    Не retryable: токен, права, битый запрос, блокировка.
    """
    return isinstance(error, (TransientError, RateLimitedError))


def is_token_problem(error: MetaApiError) -> bool:
    """True, если причина — проблема с токеном или Vision-сессией.

    В обоих случаях нужна перезагрузка Vision-сессии для получения свежего токена.
    """
    return isinstance(error, (TokenInvalidatedError, SessionDeadError))


def recommended_retry_delay(error: MetaApiError, attempt: int) -> float:
    """Рекомендуемая задержка в секундах перед следующей попыткой.

    Args:
        error:   Экземпляр MetaApiError
        attempt: Номер попытки, начиная с 1

    Returns:
        Секунды до следующей попытки. 0.0 — если ошибка не retryable.

    Стратегии:
        RateLimitedError → 30 * attempt (Meta рекомендует ждать дольше)
        TransientError   → 5 * attempt  (короткий backoff)
        иначе            → 0.0          (не повторять)
    """
    if not isinstance(attempt, int) or attempt < 1:
        attempt = 1

    if isinstance(error, RateLimitedError):
        return 30.0 * attempt
    if isinstance(error, TransientError):
        return 5.0 * attempt
    return 0.0
