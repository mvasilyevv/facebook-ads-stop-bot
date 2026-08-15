from __future__ import annotations

import json

import httpx
import pytest

from core.config import Settings
from core.telegram.gateway import (
    TelegramFailureKind,
    TelegramGatewayError,
    TelegramHTMLGateway,
)


@pytest.mark.asyncio
async def test_gateway_uses_html_send_message_without_rich_fallback() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = TelegramHTMLGateway("123456:test-token", http_client=client)

    result = await gateway.send_message(chat_id=7, text="<b>Alert</b>")

    assert result["message_id"] == 42
    assert requests[0].url.path.endswith("/sendMessage")
    assert b'"parse_mode":"HTML"' in requests[0].content
    assert b"rich_message" not in requests[0].content
    await client.aclose()


@pytest.mark.asyncio
async def test_gateway_edit_uses_html_text_contract() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": True})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = TelegramHTMLGateway("123456:test-token", http_client=client)
    await gateway.edit_message(chat_id=7, message_id=9, text="<b>Resolved</b>")

    assert requests[0].url.path.endswith("/editMessageText")
    assert b'"text":"<b>Resolved</b>"' in requests[0].content
    assert b"rich_message" not in requests[0].content
    await client.aclose()


@pytest.mark.asyncio
async def test_gateway_exposes_full_retry_after_without_sleeping() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            429,
            json={
                "ok": False,
                "error_code": 429,
                "description": "Too Many Requests",
                "parameters": {"retry_after": 137},
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = TelegramHTMLGateway("123456:test-token", http_client=client)
    with pytest.raises(TelegramGatewayError) as caught:
        await gateway.send_message(chat_id=7, text="Alert")

    assert caught.value.kind is TelegramFailureKind.RATE_LIMITED
    assert caught.value.retry_after == 137
    await client.aclose()


@pytest.mark.asyncio
async def test_gateway_never_leaks_token_in_error_or_repr() -> None:
    token = "123456:super-secret-token"

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError(f"failed {request.url}", request=request)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = TelegramHTMLGateway(token, http_client=client)
    with pytest.raises(TelegramGatewayError) as caught:
        await gateway.send_message(chat_id=7, text="Alert")

    assert token not in str(caught.value)
    assert token not in repr(gateway)
    assert caught.value.__cause__ is None
    assert caught.value.kind is TelegramFailureKind.UNKNOWN
    await client.aclose()


@pytest.mark.asyncio
async def test_gateway_redacts_action_and_navigation_capabilities_from_4xx() -> None:
    action_token = "AbCdEfGhIjKlMnOpQrStUv"
    navigation_token = "VwXyZaBcDeFgHiJkLmNoPq"

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            400,
            json={
                "ok": False,
                "error_code": 400,
                "description": (
                    f"Bad Request callback a:{action_token} "
                    f"url=https://operator.example/tma?nav={navigation_token}"
                ),
            },
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = TelegramHTMLGateway("123456:test-token", http_client=client)
    with pytest.raises(TelegramGatewayError) as caught:
        await gateway.send_message(
            chat_id=7,
            text="Alert",
            reply_markup={
                "inline_keyboard": [
                    [
                        {"text": "Action", "callback_data": f"a:{action_token}"},
                        {
                            "text": "Open",
                            "url": f"https://operator.example/tma?nav={navigation_token}",
                        },
                    ]
                ]
            },
        )

    assert action_token not in caught.value.description
    assert navigation_token not in caught.value.description
    assert action_token not in str(caught.value)
    assert navigation_token not in str(caught.value)
    assert "Bad Request" in caught.value.description
    await client.aclose()


@pytest.mark.asyncio
async def test_ok_response_without_positive_message_id_is_unknown() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 0}})

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = TelegramHTMLGateway("123456:test-token", http_client=client)
    with pytest.raises(TelegramGatewayError) as caught:
        await gateway.send_message(chat_id=7, text="Alert")

    assert caught.value.kind is TelegramFailureKind.UNKNOWN
    await client.aclose()


@pytest.mark.asyncio
async def test_send_server_error_is_unknown_to_prevent_duplicate_message() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            503,
            json={"ok": False, "error_code": 503, "description": "unavailable"},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = TelegramHTMLGateway("123456:test-token", http_client=client)
    with pytest.raises(TelegramGatewayError) as caught:
        await gateway.send_message(chat_id=7, text="Alert")

    assert caught.value.kind is TelegramFailureKind.UNKNOWN
    await client.aclose()


@pytest.mark.asyncio
async def test_malformed_edit_response_is_retryable_because_edit_is_idempotent() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = TelegramHTMLGateway("123456:test-token", http_client=client)
    with pytest.raises(TelegramGatewayError) as caught:
        await gateway.edit_message(chat_id=7, message_id=9, text="Updated")

    assert caught.value.kind is TelegramFailureKind.TRANSIENT
    await client.aclose()


@pytest.mark.asyncio
async def test_malformed_send_response_remains_unknown_to_prevent_duplicate() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"not-json")

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = TelegramHTMLGateway("123456:test-token", http_client=client)
    with pytest.raises(TelegramGatewayError) as caught:
        await gateway.send_message(chat_id=7, text="Alert")

    assert caught.value.kind is TelegramFailureKind.UNKNOWN
    await client.aclose()


@pytest.mark.asyncio
async def test_gateway_rejects_rich_or_markdown_mode() -> None:
    gateway = TelegramHTMLGateway("123456:test-token")
    with pytest.raises(ValueError, match="HTML only"):
        await gateway.send_message(chat_id=7, text="x", parse_mode="Markdown")
    await gateway.close()


@pytest.mark.asyncio
async def test_gateway_reads_current_webhook_for_cutover_reconciliation() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"ok": True, "result": {"url": "https://app.example/webhook"}},
        )

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = TelegramHTMLGateway("123456:test-token", http_client=client)

    result = await gateway.get_webhook_info()

    assert result["url"] == "https://app.example/webhook"
    assert requests[0].url.path.endswith("/getWebhookInfo")
    await client.aclose()


@pytest.mark.asyncio
async def test_gateway_uses_configured_origin_for_real_webhook_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    token = "123456:do-not-log-this-token"
    methods: list[str] = []

    requested_urls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested_urls.append(str(request.url))
        method = request.url.path.rsplit("/", 1)[-1]
        methods.append(method)
        payload = json.loads(request.content or b"{}")
        if method == "setWebhook":
            assert payload["url"] == "https://app.example.test/webhook"
            result: object = True
        elif method == "getWebhookInfo":
            result = {"url": "https://app.example.test/webhook"}
        else:  # pragma: no cover - failure makes the request test fail
            return httpx.Response(404)
        return httpx.Response(200, json={"ok": True, "result": result})

    settings = Settings(
        _env_file=None,
        deployment_environment="rehearsal",
        telegram_bot_api_origin="http://telegram-stub:18080",
    )
    monkeypatch.setattr("core.telegram.gateway.get_settings", lambda: settings)
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), trust_env=False)
    gateway = TelegramHTMLGateway(token, http_client=client)
    try:
        await gateway.set_webhook(
            url="https://app.example.test/webhook",
            secret_token="independent_webhook_secret",
        )
        info = await gateway.get_webhook_info()
    finally:
        await client.aclose()

    assert info == {"url": "https://app.example.test/webhook"}
    assert methods == ["setWebhook", "getWebhookInfo"]
    assert all(url.startswith("http://telegram-stub:18080/bot") for url in requested_urls)
    assert token not in repr(gateway)
