# -*- coding: utf-8 -*-
"""Unit-тесты lazy инициализации MetaApiClient в telegram_poller'е.

Покрывают graceful degradation: если browser-agent оффлайн на старте, poller
продолжает работу без MetaApiClient, /ask может вызвать meta-tools, они
вернут ToolError, LLM сформирует ответ без них.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest


# Сценарий: MetaApiClient.start() прошёл успешно → возвращаем живой инстанс
@pytest.mark.asyncio
async def test_build_meta_api_client_ok() -> None:
    from apps.telegram_poller.main import _build_meta_api_client

    fake_instance = MagicMock()
    fake_instance.start = AsyncMock()
    fake_ctor = MagicMock(return_value=fake_instance)

    with patch("apps.telegram_poller.main.MetaApiClient", fake_ctor):
        result = await _build_meta_api_client()

    assert result is fake_instance
    fake_instance.start.assert_awaited_once()


# Сценарий: MetaApiClient.start() бросает (browser-agent оффлайн) → None, без exception
@pytest.mark.asyncio
async def test_build_meta_api_client_browser_agent_offline() -> None:
    from apps.telegram_poller.main import _build_meta_api_client

    fake_instance = MagicMock()
    fake_instance.start = AsyncMock(side_effect=RuntimeError("connect refused"))
    fake_ctor = MagicMock(return_value=fake_instance)

    with patch("apps.telegram_poller.main.MetaApiClient", fake_ctor):
        result = await _build_meta_api_client()

    assert result is None


# Сценарий: /ask с meta_api_client=None — handle_ask не падает, шлёт «Думаю…» и запускает Task.
# Это гарантия что poller продолжает работать когда browser-agent оффлайн.
@pytest.mark.asyncio
async def test_handle_ask_works_without_meta_api_client() -> None:
    import asyncio

    from core.telegram.handlers.ask import handle_ask

    engine = MagicMock()
    client = MagicMock()
    client.send_message = AsyncMock()

    # Чтобы фоновая Task не пошла в реальный ChatSession.ask — патчим её.
    with patch(
        "core.telegram.handlers.ask._handle_ask_background",
        new=AsyncMock(),
    ) as mocked_bg:
        await handle_ask(
            engine=engine,
            client=client,
            chat_id=100,
            message_id=1,
            thread_id=None,
            user_id=42,
            username="alice",
            args_text="что нового?",
            meta_api_client=None,  # browser-agent оффлайн
        )
        # Дать фоновой Task шанс выполниться (она пустая mock)
        pending = {t for t in asyncio.all_tasks() if t is not asyncio.current_task()}
        if pending:
            await asyncio.wait(pending, timeout=1.0)

    # «Думаю…» отправлено — main loop poller'а не упал
    sent_texts = [c.kwargs.get("text", "") for c in client.send_message.call_args_list]
    assert any("Думаю" in t for t in sent_texts), f"no 'Думаю' in {sent_texts!r}"

    # Background invocation получил meta_api_client=None — проброс работает
    mocked_bg.assert_awaited_once()
    assert mocked_bg.await_args.kwargs["meta_api_client"] is None
