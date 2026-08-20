# -*- coding: utf-8 -*-
"""Факт пересечения внешней границы: его ставит транспорт, читает вызывающий.

Вызывающий не может знать, ушёл ли запрос в Meta: между его вызовом и транспортом
у клиента лежит проверка явного кабинета, подпись одноразового гранта, живая проба
канала и предохранитель. Любой из них отказывает ДО отправки — кампания не
создаётся, объектов нет, исход REJECTED и повтор безопасен.

Пока факт отправки угадывался вызывающим («сейчас позову клиента — считаю, что
POST ушёл»), доказанный отказ до отправки записывался как потерянный ответ:
оператора звали сверять пустой кабинет, а повтор и resume запрещались навсегда.

Отметка едет ContextVar'ом, а не параметром вызова: между заливом и транспортом
стоят обёртки клиента (фенсинг задачи, аудит), и параметр пришлось бы протаскивать
через каждую. Мутируется сам объект-отметка, поэтому значение видно вызывающему
и когда клиент работает во вложенном контексте.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass


@dataclass(slots=True)
class GraphDispatchRecord:
    """Отметка «запрос передан транспорту».

    ``dispatched=False`` означает «отправки не было» — это доказательство, а не
    незнание: отметку ставит сам транспортный слой непосредственно перед вызовом.
    """

    dispatched: bool = False


_GRAPH_DISPATCH: ContextVar[GraphDispatchRecord | None] = ContextVar(
    "meta_api_graph_dispatch",
    default=None,
)


@contextmanager
def observe_graph_dispatch() -> Iterator[GraphDispatchRecord]:
    """Наблюдать, ушёл ли Graph-запрос в транспорт внутри блока.

    Отметка читается и после исключения: именно отказавший вызов и есть предмет
    вопроса «успело ли уйти».
    """
    record = GraphDispatchRecord()
    token = _GRAPH_DISPATCH.set(record)
    try:
        yield record
    finally:
        _GRAPH_DISPATCH.reset(token)


def mark_graph_dispatched() -> None:
    """Отметить, что запрос ушёл в транспорт.

    Зовёт только транспортный слой и только непосредственно перед вызовом. Всё,
    что отказало выше по стеку, отказало до отправки.
    """
    record = _GRAPH_DISPATCH.get()
    if record is not None:
        record.dispatched = True
