# -*- coding: utf-8 -*-
"""Текст отказа Meta диагностируем в логе и не утекает в запись задачи.

Инвариант появился из живого падения 20.08.2026: залив вставал с
``code=100 subcode=1885316``, и это было ВСЁ, что оставалось оператору —
Graph-текст отбрасывался на этапе классификации, потому что может нести
access_token. Без текста причина отказа Meta неустановима ни для оператора,
ни для разработчика, а сам код ошибки ничего не объясняет.

Два требования одновременно:

1. ``str(exc)`` и ``repr(exc)`` — это то, что доезжает до ``last_error``
   задачи и до карточки инцидента. Там Graph-текста быть не должно.
2. Санитизированный текст обязан сохраниться отдельным полем, иначе
   ``exc_info`` в логе воркера по-прежнему не назовёт причину.
"""

from __future__ import annotations

from core.meta_api.errors import PermanentError, classify_graph_error

_GRAPH_TEXT = (
    "Invalid parameter: campaign limit reached "
    "(access_token=EAAG1234567890abcdefXYZ), fbtrace_id=A1b2C3d4E5f"
)


def test_graph_text_never_reaches_task_record() -> None:
    """str/repr исключения не несут Graph-текст: они едут в запись задачи."""
    exc = classify_graph_error(
        100,
        1885316,
        _GRAPH_TEXT,
        endpoint="/act_1/campaigns",
        fbtrace_id="A1b2C3d4E5f",
    )

    for rendered in (str(exc), repr(exc)):
        assert "campaign limit reached" not in rendered
        assert "EAAG1234567890abcdefXYZ" not in rendered


def test_graph_text_survives_for_the_worker_log() -> None:
    """Причина отказа Meta остаётся доступной — иначе диагностировать нечем."""
    exc = classify_graph_error(
        100,
        1885316,
        _GRAPH_TEXT,
        endpoint="/act_1/campaigns",
        fbtrace_id="A1b2C3d4E5f",
    )

    assert isinstance(exc, PermanentError)
    assert exc.meta_message is not None
    # Смысл отказа сохранён…
    assert "campaign limit reached" in exc.meta_message
    # …а секрет из того же текста — нет.
    assert "EAAG1234567890abcdefXYZ" not in exc.meta_message


def test_bare_meta_access_token_is_redacted() -> None:
    """Голый EAA-токен без имени поля тоже вырезается."""
    exc = classify_graph_error(
        100,
        None,
        "Session for EAAG1234567890abcdefXYZ is invalid",
        endpoint="/act_1/campaigns",
    )

    assert exc.meta_message is not None
    assert "EAAG1234567890abcdefXYZ" not in exc.meta_message


def test_missing_graph_text_is_absence_not_empty_string() -> None:
    """Нет текста — это ``None``, а не пустая строка: пустое можно принять за «Meta промолчала»."""
    exc = classify_graph_error(100, None, "", endpoint="/act_1/campaigns")

    assert exc.meta_message is None
