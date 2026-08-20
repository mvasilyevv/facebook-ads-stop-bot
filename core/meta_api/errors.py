# -*- coding: utf-8 -*-
"""Доменные исключения Marketing API и маппинг Graph error codes на них.

Используется в client.py для конверсии Graph API errors в pythonic exceptions
и в worker'ах — для решения retry vs final fail.
"""

from __future__ import annotations

import re


class MutationValidationError(ValueError):
    """Ошибка валидации payload в mutation handler'е.

    Используется когда handler осознанно отвергает payload из-за недопустимого
    значения (неверный формат id, отсутствует обязательная секция, значение вне
    допустимого диапазона и т.п.).

    worker маршрутизирует MutationValidationError → mark_failed (permanent):
    повторный retry с тем же payload смысла не имеет.

    Голый ValueError (случайный, из-за бага в коде или неожиданного Graph-ответа)
    НЕ является MutationValidationError и попадёт в transient/requeue ветку.
    """


class MetaApiError(RuntimeError):
    """Базовое исключение Marketing API."""

    def __init__(
        self,
        message: str,
        *,
        code: int | None = None,
        subcode: int | None = None,
        endpoint: str | None = None,
        fbtrace_id: str | None = None,
    ) -> None:
        self.code = code
        self.subcode = subcode
        self.endpoint = endpoint
        self.fbtrace_id = fbtrace_id
        super().__init__(message)

    def __repr__(self) -> str:
        return (
            f"{type(self).__name__}(code={self.code}, subcode={self.subcode}, "
            f"endpoint={self.endpoint!r}, fbtrace={self.fbtrace_id!r}, "
            f"message={super().__str__()!r})"
        )


class TemporaryError(MetaApiError):
    """Временная ошибка — стоит retry."""


class AmbiguousResultError(TemporaryError):
    """The external request may have committed but its response was lost."""


class PermanentError(MetaApiError):
    """Постоянная ошибка — retry бесполезен (mark_failed сразу)."""


class TokenInvalidError(PermanentError):
    """Токен невалиден или revoked.

    Graph codes: 190, 102, 463, 464, 467, subcode 1357045.
    Действие: алерт в TG, требуется ручной re-login Vision.
    """


class LoginRequiredError(TokenInvalidError):
    """Профиль Vision РАЗЛОГИНЕН / чекпоинт — нужен ре-логин, не просто обновление токена.

    Отличается от рядового TokenInvalidError тем, что сессия целиком протухла (redirect
    на login.php/checkpoint, HTML вместо JSON, 190 с login-subcode 458/459/460/463/464/467).
    Re-sniff токена НЕ помогает — оператор должен зайти в Vision и залогиниться заново.

    Наследник TokenInvalidError → для meta_api_worker это Permanent-класс (mark_failed,
    без бесконечного retry), но terminal projection остаётся отдельной от обычного
    token-invalid и permission failure.
    Money-критично: слепой канал = слитый бюджет (инцидент 01.07 — канал умер молча).
    """


class RateLimitedError(TemporaryError):
    """Meta rate-limit или throttling.

    Graph codes: 4, 17, 32, 613, 80004. Retry с backoff.
    """


class NotFoundError(PermanentError):
    """Объект не существует (ad/adset/campaign удалён).

    Graph code 803 или 100 с subcode 33.
    """


class PermissionError(PermanentError):  # noqa: A001
    """Нет прав на действие (например, объект не из нашего ad account).

    Graph codes: 200, 270, 272.
    """


class SessionUnavailableError(TemporaryError):
    """Vision-сессия не активна или токен не найден на странице.

    Возвращается health-check'ом и executeGraphCall при detail='token_not_found'
    или 'session_not_found'.
    """


class PreDispatchRejectedError(SessionUnavailableError):
    """Отказ, про который известно, что запрос во внешнюю систему НЕ уходил.

    Исход такого отказа — REJECTED: объектов не создано, побочного эффекта нет,
    повтор безопасен. Записать здесь UNKNOWN значит потребовать от оператора
    ручной сверки там, где сверять нечего (прод 19.08.2026: залив упал на
    исчерпанном дедлайне ДО первого POST, а карточка сказала «Meta могла принять
    часть изменений» при нуле созданных объектов).

    Наследование от SessionUnavailableError оставляет прежними все ветки,
    построенные на «канал не готов, отправки не было»: worker'ы уже считают эту
    семью доказанным pre-send отказом.
    """


# Код причины → та же причина человеческим языком. Зеркалит
# OPERATION_REJECTION_PREDICATES в services/browser-agent/src/meta-api/service.ts:
# разошедшийся словарь молча вернёт отказ в путь «часть изменений принята».
#
# Словарь закрытый и живёт здесь, рядом с исключением, а не в транспортном клиенте:
# читает его и карточка инцидента оператора, которой gRPC-слой ни к чему. Причина
# доезжает до оператора только отсюда — сырой ``details()`` от browser-agent наружу
# не выносится, в нём изредка лежит токен из Graph-ответа.
BROWSER_OPERATION_REJECTION_REASONS: dict[str, str] = {
    "capability_authority_unavailable": "сервис выдачи разрешений на операцию недоступен",
    "capability_contract_incompatible": "версия контракта браузера не совпадает",
    "capability_secret_unavailable": "ключ подписи разрешения недоступен в браузере",
    "capability_cabinet_mismatch": "разрешение выдано на другой рекламный кабинет",
    "caller_not_authorized": "операция не разрешена вызывающему сервису",
    "capability_task_binding_invalid": "разрешение не привязано к задаче",
    "capability_lease_binding_invalid": "разрешение не привязано к аренде задачи",
    "capability_expired": "срок действия разрешения на операцию истёк",
    "capability_malformed": "разрешение на операцию повреждено",
    "capability_signature_invalid": "подпись разрешения на операцию не сошлась",
    "capability_invalid": "разрешение на операцию недействительно",
    "ownership_preflight_rejected": "цель операции не принадлежит этому кабинету",
    "graph_method_override": "подмена HTTP-метода запроса не разрешена",
    "graph_method_semantics": "метод запроса задан неоднозначно",
    "graph_get_body": "запрос на чтение пришёл с телом",
    "graph_endpoint_query": "адрес запроса содержит недопустимые параметры",
}


class BrowserOperationRejectedError(PreDispatchRejectedError):
    """Собственная авторизация browser-agent отвергла операцию до отправки в Meta.

    Каждый предикат этой семьи (недействительный или истёкший грант, чужой
    кабинет, неавторизованный вызывающий, отказ ownership-preflight, нарушенная
    семантика Graph-запроса) срабатывает раньше, чем открывается внешняя
    граница. Значит исход — REJECTED: побочного эффекта нет, перечень уже
    созданного не пополнился, и оператору нечего сверять по этому запросу.

    ``reason_code`` — машинный код причины из закрытого словаря
    ``BROWSER_OPERATION_REJECTION_REASONS``; текст исключения — та же причина
    человеческим языком. Сырой ``details()`` от browser-agent не переносится:
    он свободный и изредка содержит токен из Graph-ответа.
    """

    def __init__(
        self,
        message: str,
        *,
        reason_code: str,
        endpoint: str | None = None,
    ) -> None:
        self.reason_code = reason_code
        super().__init__(message, endpoint=endpoint)


class BrowserReadinessRejectedError(PreDispatchRejectedError):
    """Exact live v5/profile/session check rejected before the controlled RPC.

    Отдельный лист нужен ровно для одного: задача возвращается в очередь без
    сгорания попытки, а claim'нутая готовность канала гасится. Прочие доказанные
    pre-send отказы (исчерпанный дедлайн, отказ выдачи гранта) попытку тратят —
    повторять их без ограничения нельзя.
    """


# Маппинг Graph code → класс исключения. Default — PermanentError.
_CODE_MAP: dict[int, type[MetaApiError]] = {
    # Отрицательные коды — ВНУТРЕННИЕ сигналы browser-agent (реальные Graph-коды
    # положительные). Все транзиентные, но только доказанный pre-send
    # сбой можно слепо повторять; mid-flight потери требуют UNKNOWN/reconcile.
    #   -1 TokenNotFound (EAA-токен ещё не в DOM свежей вкладки) — fetch не звался;
    #   -2 NetworkError (Failed to fetch / Timeout fetch внутри page.evaluate) — сетевой блип;
    #   -3 page-evaluate error — page/сессия в переходном состоянии;
    #   -4 отказ, случившийся до старта fetch (отмена/предусловие) — отправки не было
    #      (browser-agent: CancelledBeforeSend).
    -1: PreDispatchRejectedError,
    -2: AmbiguousResultError,
    # A page/context can disappear after fetch reached Meta but before
    # page.evaluate returned the response.  Unlike token-not-found (-1), this is
    # not proof of a pre-send failure and must never enter the blind-retry path.
    -3: AmbiguousResultError,
    # Доказанный pre-send отказ на стороне browser-agent. Отдельный код нужен
    # именно потому, что -2 склеивал «fetch не стартовал» с «ответ потерян» и
    # обе ситуации уходили оператору как ручная сверка.
    -4: PreDispatchRejectedError,
    1: PermanentError,
    2: TemporaryError,
    4: RateLimitedError,
    17: RateLimitedError,
    32: RateLimitedError,
    100: PermanentError,  # часто subcode=33 → NotFoundError (см. _SUBCODE_OVERRIDES)
    102: TokenInvalidError,
    190: TokenInvalidError,
    200: PermissionError,
    270: PermissionError,
    272: PermissionError,
    368: PermanentError,  # action blocked
    463: TokenInvalidError,
    464: TokenInvalidError,
    467: TokenInvalidError,
    613: RateLimitedError,
    803: NotFoundError,
    80004: RateLimitedError,
}

# Subcode override применяется ПЕРЕД code lookup.
# Login-subcodes 458/459/460/463/464/467 — именно разлогин/чекпоинт (сессия протухла,
# нужен ре-логин профиля), а не рядовое протухание короткоживущего токена → LoginRequiredError.
# Зеркалит browser-agent _LOGIN_REQUIRED_SUBCODES (am-fetch.ts / meta-api/client.ts).
_SUBCODE_OVERRIDES: dict[int, type[MetaApiError]] = {
    33: NotFoundError,  # 100/33 = object doesn't exist
    458: LoginRequiredError,
    459: LoginRequiredError,  # checkpoint (user must log in)
    460: LoginRequiredError,  # password changed → session invalidated
    463: LoginRequiredError,  # session expired
    464: LoginRequiredError,  # unconfirmed user
    467: LoginRequiredError,  # invalid access token (logged out)
    1357045: TokenInvalidError,  # session re-auth required
}

# «session has been invalidated» и «changed their password» — канонический ответ Meta
# при смене пароля или принудительном сбросе сессии (прод 18.08.2026: 4.5 часа слепого
# канала классифицировались как рядовое протухание токена). Зеркалит browser-agent.
_LOGIN_REQUIRED_MESSAGE_RE = re.compile(
    r"session.*(expired|invalidated)|changed (their|your) password"
    r"|log ?in|checkpoint|re-?authenticate|not logged in|logged out",
    re.IGNORECASE,
)


def classify_graph_error(
    code: int | None,
    subcode: int | None,
    message: str,
    *,
    endpoint: str | None = None,
    fbtrace_id: str | None = None,
) -> MetaApiError:
    """Преобразовать Graph error в pythonic exception.

    Логика: subcode override → code lookup → дефолт.
    Дефолт: code 0/None → TemporaryError (могла быть сеть), иначе PermanentError.
    """
    exc_cls: type[MetaApiError]
    if subcode and subcode in _SUBCODE_OVERRIDES:
        exc_cls = _SUBCODE_OVERRIDES[subcode]
    elif code == 190 and _LOGIN_REQUIRED_MESSAGE_RE.search(message or ""):
        # Some Graph 190 responses omit error_subcode but still state that the
        # browser session is logged out. Keep this aligned with browser-agent's
        # isLoginRequiredError() so the same failure gets the same incident.
        exc_cls = LoginRequiredError
    elif code and code in _CODE_MAP:
        exc_cls = _CODE_MAP[code]
    else:
        # code 0/None — могла быть сеть → Temporary. Отрицательные коды — внутренние
        # сигналы browser-agent (Graph-коды положительные) → транзиентные, retry, а не
        # permanent-fail (backstop для будущих негативных кодов помимо явных в _CODE_MAP).
        exc_cls = TemporaryError if (not code or code < 0) else PermanentError
    # message использован ТОЛЬКО для классификации выше (subcode/code/regex на полном
    # тексте) — Graph-текст может содержать access_token или другие секреты, поэтому в
    # тело исключения (str(exc): логи, audit, UI) он не попадает, остаётся код ошибки.
    return exc_cls(
        f"Graph API error code={code} subcode={subcode}",
        code=code,
        subcode=subcode,
        endpoint=endpoint,
        fbtrace_id=fbtrace_id,
    )
