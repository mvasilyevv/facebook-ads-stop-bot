# -*- coding: utf-8 -*-
"""Unit-тесты для MetaApiHighLevelClient.

Все gRPC-вызовы мокируются через AsyncMock — реальный gRPC не поднимается.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from clients.python_grpc.meta_api_client import MetaApiError
from core.browser.circuit_breaker import AsyncCircuitBreaker, CircuitOpenError
from core.meta_api.client import MetaApiHighLevelClient

# ─── Фабрики ───────────────────────────────────────────────────────────────


def _make_graph_result(response: dict, *, status_code: int = 200, duration_ms: int = 50):
    """Сконструировать заглушку GraphCallResult."""
    result = MagicMock()
    result.response = response
    result.status_code = status_code
    result.duration_ms = duration_ms
    return result


def _make_meta_error(
    message: str = "Ошибка",
    *,
    code: int = 0,
    subcode: int = 0,
    type_: str = "",
) -> MetaApiError:
    return MetaApiError(message, code=code, subcode=subcode, type_=type_)


def _make_client(*, max_attempts: int = 3) -> tuple[MetaApiHighLevelClient, AsyncMock]:
    """Вернуть клиент + мок низкоуровневого execute_graph_call."""
    client = MetaApiHighLevelClient(max_attempts=max_attempts)
    mock_ll = AsyncMock()
    client._low_level = mock_ll
    return client, mock_ll


# ─── Базовые вызовы ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_success_no_retry():
    # Успешный вызов возвращает response без единой попытки retry
    client, mock_ll = _make_client()
    mock_ll.execute_graph_call = AsyncMock(
        return_value=_make_graph_result({"id": "123", "name": "Test"})
    )

    result = await client.execute("GET", "/me")

    assert result == {"id": "123", "name": "Test"}
    mock_ll.execute_graph_call.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_passes_all_params():
    # Все параметры execute() корректно передаются в низкоуровневый клиент
    client, mock_ll = _make_client()
    mock_ll.execute_graph_call = AsyncMock(return_value=_make_graph_result({}))

    await client.execute(
        "POST",
        "/act_123/adsets",
        {"fields": "id,name"},
        body_json='{"status":"PAUSED"}',
        timeout_ms=5000,
        session_id="sess-abc",
        initiated_by="test",
    )

    call_kwargs = mock_ll.execute_graph_call.call_args
    assert call_kwargs.args[0] == "POST"
    assert call_kwargs.args[1] == "/act_123/adsets"
    assert call_kwargs.kwargs.get("body_json") == '{"status":"PAUSED"}'
    assert call_kwargs.kwargs.get("timeout_ms") == 5000
    assert call_kwargs.kwargs.get("session_id") == "sess-abc"


# ─── Ошибки без retry ──────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "code,label",
    [
        (190, "token_invalidated"),  # is_token_invalidated
        (-1, "session_dead"),  # is_session_dead
        (100, "invalid_params"),  # _is_invalid_params
        (1, "invalid_params_code1"),  # _is_invalid_params
    ],
)
@pytest.mark.asyncio
async def test_execute_non_retryable_errors(code: int, label: str):
    # Перечисленные коды → немедленное исключение, ровно 1 вызов gRPC
    client, mock_ll = _make_client()
    err = _make_meta_error("err", code=code)
    mock_ll.execute_graph_call = AsyncMock(side_effect=err)

    with pytest.raises(MetaApiError) as exc_info:
        await client.execute("GET", "/me")

    assert exc_info.value.code == code
    assert mock_ll.execute_graph_call.await_count == 1


# ─── Rate limited ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_rate_limited_retries_with_backoff(monkeypatch):
    # Rate limit (code=17) → retry до max_attempts с backoff 30*attempt сек
    client, mock_ll = _make_client(max_attempts=3)
    err = _make_meta_error("Rate limit", code=17)

    # Первые 2 вызова — rate limit, 3й — успех
    mock_ll.execute_graph_call = AsyncMock(side_effect=[err, err, _make_graph_result({"ok": True})])

    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr("core.meta_api.client.asyncio.sleep", fake_sleep)

    result = await client.execute("GET", "/me")

    assert result == {"ok": True}
    assert mock_ll.execute_graph_call.await_count == 3
    # backoff: attempt=1 → 30сек, attempt=2 → 60сек
    assert sleep_calls == [30.0, 60.0]


@pytest.mark.asyncio
async def test_execute_rate_limited_exhausts_attempts(monkeypatch):
    # Rate limit все max_attempts → поднимает MetaApiError после последней попытки
    client, mock_ll = _make_client(max_attempts=2)
    err = _make_meta_error("Rate limit", code=17)
    mock_ll.execute_graph_call = AsyncMock(side_effect=err)

    async def fake_sleep(_: float) -> None:
        pass

    monkeypatch.setattr("core.meta_api.client.asyncio.sleep", fake_sleep)

    with pytest.raises(MetaApiError) as exc_info:
        await client.execute("GET", "/me")

    assert exc_info.value.code == 17
    assert mock_ll.execute_graph_call.await_count == 2


# ─── Transient error ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_execute_transient_retries_exponential_backoff(monkeypatch):
    # Transient (code=2) → retry с backoff 5 * 2^(attempt-1): 5, 10, 20
    client, mock_ll = _make_client(max_attempts=3)
    err = _make_meta_error("Service exception", code=2)
    mock_ll.execute_graph_call = AsyncMock(side_effect=[err, err, _make_graph_result({"data": []})])

    sleep_calls: list[float] = []

    async def fake_sleep(delay: float) -> None:
        sleep_calls.append(delay)

    monkeypatch.setattr("core.meta_api.client.asyncio.sleep", fake_sleep)

    result = await client.execute("GET", "/act_123/insights")

    assert result == {"data": []}
    assert mock_ll.execute_graph_call.await_count == 3
    # attempt=1 → 5.0 сек, attempt=2 → 10.0 сек
    assert sleep_calls == [5.0, 10.0]


@pytest.mark.asyncio
async def test_execute_final_failure_after_max_attempts(monkeypatch):
    # Все попытки transient → финальная MetaApiError
    client, mock_ll = _make_client(max_attempts=3)
    err = _make_meta_error("Service exception", code=2)
    mock_ll.execute_graph_call = AsyncMock(side_effect=err)

    async def fake_sleep(_: float) -> None:
        pass

    monkeypatch.setattr("core.meta_api.client.asyncio.sleep", fake_sleep)

    with pytest.raises(MetaApiError):
        await client.execute("GET", "/me")

    assert mock_ll.execute_graph_call.await_count == 3


# ─── Circuit breaker ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_circuit_breaker_opens_after_3_failures():
    # После 3 неудач подряд circuit-breaker переходит в OPEN
    cb = AsyncCircuitBreaker(name="test-cb", failure_threshold=3, recovery_timeout=60.0)
    client = MetaApiHighLevelClient(circuit_breaker=cb)
    err = _make_meta_error("Service exception", code=2)
    client._low_level.execute_graph_call = AsyncMock(side_effect=err)  # type: ignore[attr-defined]

    from core.browser.circuit_breaker import CircuitState

    # Первые 3 вызова — накапливаем фейлы (без sleep, пропускаем retry через max_attempts=1)
    one_shot_client = MetaApiHighLevelClient(max_attempts=1, circuit_breaker=cb)
    one_shot_client._low_level = client._low_level  # тот же мок

    for _ in range(3):
        with pytest.raises(MetaApiError):
            await one_shot_client.execute("GET", "/me")

    assert cb.state == CircuitState.OPEN


@pytest.mark.asyncio
async def test_circuit_breaker_open_raises_immediately():
    # Когда circuit-breaker OPEN — CircuitOpenError без вызова gRPC
    cb = AsyncCircuitBreaker(name="test-cb", failure_threshold=1, recovery_timeout=60.0)
    client = MetaApiHighLevelClient(max_attempts=1, circuit_breaker=cb)
    err = _make_meta_error("fail", code=2)
    client._low_level.execute_graph_call = AsyncMock(side_effect=err)  # type: ignore[attr-defined]

    # Открываем circuit-breaker
    with pytest.raises(MetaApiError):
        await client.execute("GET", "/me")

    # Следующий вызов должен падать немедленно с CircuitOpenError
    with pytest.raises(CircuitOpenError):
        await client.execute("GET", "/me")

    # gRPC должен быть вызван ровно 1 раз (только первый вызов)
    assert client._low_level.execute_graph_call.await_count == 1  # type: ignore[attr-defined]


# ─── me() ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_me_returns_response():
    # me() вызывает GET /me и возвращает словарь
    client, mock_ll = _make_client()
    mock_ll.execute_graph_call = AsyncMock(
        return_value=_make_graph_result({"id": "111", "name": "Иван"})
    )

    result = await client.me()

    assert result == {"id": "111", "name": "Иван"}
    call = mock_ll.execute_graph_call.call_args
    assert call.args[0] == "GET"
    assert call.args[1] == "/me"


# ─── list_ad_accounts() ────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_ad_accounts_returns_data_list():
    # list_ad_accounts() возвращает список из ответа под ключом "data"
    client, mock_ll = _make_client()
    accounts = [{"id": "act_1"}, {"id": "act_2"}]
    mock_ll.execute_graph_call = AsyncMock(return_value=_make_graph_result({"data": accounts}))

    result = await client.list_ad_accounts()

    assert result == accounts
    call = mock_ll.execute_graph_call.call_args
    assert call.args[1] == "/me/adaccounts"


# ─── get_insights() ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_get_insights_default_params():
    # get_insights() с дефолтными параметрами передаёт правильный endpoint и level
    client, mock_ll = _make_client()
    mock_ll.execute_graph_call = AsyncMock(
        return_value=_make_graph_result({"data": [{"ad_id": "1"}]})
    )

    result = await client.get_insights("12345")

    assert result == [{"ad_id": "1"}]
    call = mock_ll.execute_graph_call.call_args
    assert call.args[1] == "/act_12345/insights"
    params = call.args[2]
    assert params["level"] == "ad"
    assert params["date_preset"] == "today"


@pytest.mark.asyncio
async def test_get_insights_prepends_act_prefix():
    # get_insights() добавляет "act_" к account_id если его нет
    client, mock_ll = _make_client()
    mock_ll.execute_graph_call = AsyncMock(return_value=_make_graph_result({"data": []}))

    await client.get_insights("act_777")  # уже с act_

    call = mock_ll.execute_graph_call.call_args
    assert call.args[1] == "/act_777/insights"  # без дублирования


@pytest.mark.asyncio
async def test_get_insights_default_attribution_windows_no_deprecated():
    # Дефолтные attribution windows: содержит 1d_click, 7d_click, 1d_view
    # и НЕ содержит 7d_view и 28d_view (удалены Meta 12 янв 2026)
    client, mock_ll = _make_client()
    mock_ll.execute_graph_call = AsyncMock(return_value=_make_graph_result({"data": []}))

    await client.get_insights("act_100")

    call = mock_ll.execute_graph_call.call_args
    windows_raw = call.args[2]["action_attribution_windows"]
    windows = json.loads(windows_raw)

    assert "1d_click" in windows
    assert "7d_click" in windows
    assert "1d_view" in windows
    assert "7d_view" not in windows
    assert "28d_view" not in windows


@pytest.mark.asyncio
async def test_get_insights_filters_deprecated_windows_from_input():
    # Если в action_attribution_windows передали deprecated значения — они отфильтруются
    client, mock_ll = _make_client()
    mock_ll.execute_graph_call = AsyncMock(return_value=_make_graph_result({"data": []}))

    await client.get_insights(
        "act_100",
        action_attribution_windows=["1d_click", "7d_view", "28d_view"],
    )

    call = mock_ll.execute_graph_call.call_args
    windows = json.loads(call.args[2]["action_attribution_windows"])
    assert "7d_view" not in windows
    assert "28d_view" not in windows
    assert "1d_click" in windows


@pytest.mark.asyncio
async def test_get_insights_custom_level_and_date_preset():
    # Кастомный level и date_preset передаются без изменений
    client, mock_ll = _make_client()
    mock_ll.execute_graph_call = AsyncMock(return_value=_make_graph_result({"data": []}))

    await client.get_insights("act_200", level="campaign", date_preset="last_7d")

    call = mock_ll.execute_graph_call.call_args
    params = call.args[2]
    assert params["level"] == "campaign"
    assert params["date_preset"] == "last_7d"


# ─── pause_entity() ────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_pause_entity_posts_correct_body():
    # pause_entity() делает POST /{id} с body {"status": "PAUSED"}
    client, mock_ll = _make_client()
    mock_ll.execute_graph_call = AsyncMock(return_value=_make_graph_result({"success": True}))

    result = await client.pause_entity("120203456789")

    call = mock_ll.execute_graph_call.call_args
    assert call.args[0] == "POST"
    assert call.args[1] == "/120203456789"
    body = json.loads(call.kwargs["body_json"])
    assert body["status"] == "PAUSED"
    assert result == {"success": True}


# ─── activate_entity() ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_activate_entity_posts_correct_body():
    # activate_entity() делает POST /{id} с body {"status": "ACTIVE"}
    client, mock_ll = _make_client()
    mock_ll.execute_graph_call = AsyncMock(return_value=_make_graph_result({"success": True}))

    await client.activate_entity("120203456789")

    call = mock_ll.execute_graph_call.call_args
    assert call.args[0] == "POST"
    body = json.loads(call.kwargs["body_json"])
    assert body["status"] == "ACTIVE"


# ─── set_budget() ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_set_budget_daily():
    # set_budget() с daily_budget_cents передаёт правильный payload
    client, mock_ll = _make_client()
    mock_ll.execute_graph_call = AsyncMock(return_value=_make_graph_result({"id": "adset_123"}))

    await client.set_budget("adset_123", daily_budget_cents=500_00)

    call = mock_ll.execute_graph_call.call_args
    assert call.args[0] == "POST"
    assert call.args[1] == "/adset_123"
    body = json.loads(call.kwargs["body_json"])
    assert body["daily_budget"] == 50000
    assert "lifetime_budget" not in body


@pytest.mark.asyncio
async def test_set_budget_lifetime():
    # set_budget() с lifetime_budget_cents передаёт lifetime_budget
    client, mock_ll = _make_client()
    mock_ll.execute_graph_call = AsyncMock(return_value=_make_graph_result({"id": "adset_456"}))

    await client.set_budget("adset_456", lifetime_budget_cents=10_000_00)

    call = mock_ll.execute_graph_call.call_args
    body = json.loads(call.kwargs["body_json"])
    assert body["lifetime_budget"] == 1_000_000
    assert "daily_budget" not in body


# ─── duplicate_campaign() ──────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_duplicate_campaign_default_params():
    # duplicate_campaign() делает POST /{id}/copies с deep_copy=True по умолчанию
    client, mock_ll = _make_client()
    mock_ll.execute_graph_call = AsyncMock(
        return_value=_make_graph_result({"copied_campaign_id": "new_id"})
    )

    result = await client.duplicate_campaign("campaign_999")

    call = mock_ll.execute_graph_call.call_args
    assert call.args[0] == "POST"
    assert call.args[1] == "/campaign_999/copies"
    body = json.loads(call.kwargs["body_json"])
    assert body["deep_copy"] is True
    assert result == {"copied_campaign_id": "new_id"}


@pytest.mark.asyncio
async def test_duplicate_campaign_with_rename_options():
    # duplicate_campaign() передаёт rename_options если указаны
    client, mock_ll = _make_client()
    mock_ll.execute_graph_call = AsyncMock(return_value=_make_graph_result({}))

    rename = {"rename_strategy": "UNDERLINE_SUFFIX", "rename_prefix": "COPY"}
    await client.duplicate_campaign("camp_100", deep_copy=False, rename_options=rename)

    call = mock_ll.execute_graph_call.call_args
    body = json.loads(call.kwargs["body_json"])
    assert body["deep_copy"] is False
    assert body["rename_options"] == rename


# ─── context manager ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_context_manager_calls_start_and_close():
    # async with MetaApiHighLevelClient() вызывает start() и close()
    client = MetaApiHighLevelClient()
    client._low_level = AsyncMock()  # type: ignore[attr-defined]

    async with client:
        pass

    client._low_level.start.assert_awaited_once()  # type: ignore[attr-defined]
    client._low_level.close.assert_awaited_once()  # type: ignore[attr-defined]
