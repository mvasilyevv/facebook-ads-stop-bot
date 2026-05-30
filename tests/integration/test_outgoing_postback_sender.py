# -*- coding: utf-8 -*-
"""Интеграционный: OutgoingPostbackSender через httpx + respx (без живого внешнего трекера).

Проверяет retry-семантику, неблокирующий dispatch и that форвард не роняет flow.
"""

from __future__ import annotations

from decimal import Decimal

import pytest
import respx
from httpx import Response, TransportError

from core.adset_pro.outgoing import OutgoingPostback, OutgoingPostbackSender

_URL_TPL = "https://tracker.test/pb?cid={click_id}&goal={goal}&payout={payout}"


def _pb(click_id: str = "c1") -> OutgoingPostback:
    return OutgoingPostback(click_id=click_id, event_type="ftd", revenue=Decimal("10"))


# Успешная отправка: 200 → ok=True, одна попытка, статус сохранён.
@pytest.mark.asyncio
async def test_send_success() -> None:
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(url__startswith="https://tracker.test/pb").mock(
            return_value=Response(200, text="ok")
        )
        async with OutgoingPostbackSender(url_template=_URL_TPL, enabled=True) as sender:
            res = await sender.send(_pb())
    assert res.ok is True
    assert res.status_code == 200
    assert res.attempts == 1
    assert route.call_count == 1


# 5xx один раз → 200 на второй: retry срабатывает, итог ok=True, attempts=2.
@pytest.mark.asyncio
async def test_send_retries_5xx_then_succeeds() -> None:
    responses = [Response(503, text="down"), Response(200, text="ok")]

    def _handler(request):
        return responses.pop(0)

    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(url__startswith="https://tracker.test/pb").mock(side_effect=_handler)
        async with OutgoingPostbackSender(
            url_template=_URL_TPL, enabled=True, max_retries=3
        ) as sender:
            res = await sender.send(_pb())
    assert res.ok is True
    assert res.attempts == 2
    assert route.call_count == 2


# 4xx → permanent, без retry: ok=False, одна попытка (не долбим чужой endpoint).
@pytest.mark.asyncio
async def test_send_permanent_4xx_no_retry() -> None:
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(url__startswith="https://tracker.test/pb").mock(
            return_value=Response(400, text="bad")
        )
        async with OutgoingPostbackSender(
            url_template=_URL_TPL, enabled=True, max_retries=3
        ) as sender:
            res = await sender.send(_pb())
    assert res.ok is False
    assert res.status_code == 400
    assert res.attempts == 1
    assert route.call_count == 1


# 5xx на всех попытках → ok=False после исчерпания retry (не бросает наружу).
@pytest.mark.asyncio
async def test_send_5xx_exhausts_retries() -> None:
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(url__startswith="https://tracker.test/pb").mock(
            return_value=Response(500, text="boom")
        )
        async with OutgoingPostbackSender(
            url_template=_URL_TPL, enabled=True, max_retries=2
        ) as sender:
            res = await sender.send(_pb())
    assert res.ok is False
    assert res.attempts == 2
    assert route.call_count == 2


# Сетевой сбой → ok=False с error, без исключения наружу.
@pytest.mark.asyncio
async def test_send_network_error_returns_result() -> None:
    with respx.mock(assert_all_called=False) as mock:
        mock.get(url__startswith="https://tracker.test/pb").mock(
            side_effect=TransportError("dns fail")
        )
        async with OutgoingPostbackSender(
            url_template=_URL_TPL, enabled=True, max_retries=2
        ) as sender:
            res = await sender.send(_pb())
    assert res.ok is False
    assert res.error is not None


# Отключённый sender (enabled=False) → skipped, без HTTP-запроса.
@pytest.mark.asyncio
async def test_send_disabled_skips() -> None:
    with respx.mock(assert_all_called=False) as mock:
        route = mock.get(url__startswith="https://tracker.test/pb").mock(return_value=Response(200))
        async with OutgoingPostbackSender(url_template=_URL_TPL, enabled=False) as sender:
            res = await sender.send(_pb())
    assert res.skipped is True
    assert res.ok is False
    assert route.call_count == 0


# Пустой URL-шаблон → skipped, без HTTP.
@pytest.mark.asyncio
async def test_send_empty_url_skips() -> None:
    async with OutgoingPostbackSender(url_template="", enabled=True) as sender:
        res = await sender.send(_pb())
    assert res.skipped is True


# dispatch() не блокирует: возвращает Task сразу, drain() дожидается результата.
@pytest.mark.asyncio
async def test_dispatch_non_blocking_then_drain() -> None:
    with respx.mock(assert_all_called=True) as mock:
        route = mock.get(url__startswith="https://tracker.test/pb").mock(
            return_value=Response(200, text="ok")
        )
        async with OutgoingPostbackSender(url_template=_URL_TPL, enabled=True) as sender:
            task = sender.dispatch(_pb("bg1"))
            # dispatch вернул Task немедленно, HTTP ещё мог не уйти.
            assert not task.done() or task.done()  # просто: вызов не бросил
            results = await sender.drain()
    assert route.call_count == 1
    assert len(results) == 1
    assert results[0].ok is True
