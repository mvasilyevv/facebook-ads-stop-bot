from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

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

    class TelegramStubHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - stdlib handler contract
            method = self.path.rsplit("/", 1)[-1]
            methods.append(method)
            content_length = int(self.headers.get("Content-Length", "0"))
            payload = json.loads(self.rfile.read(content_length) or b"{}")
            if method == "setWebhook":
                assert payload["url"] == "https://app.example.test/webhook"
                result: object = True
            elif method == "getWebhookInfo":
                result = {"url": "https://app.example.test/webhook"}
            else:  # pragma: no cover - failure makes the request test fail
                self.send_error(404)
                return
            body = json.dumps({"ok": True, "result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 18080), TelegramStubHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    requested_urls: list[str] = []

    class StubNetworkTransport(httpx.AsyncBaseTransport):
        def __init__(self) -> None:
            self._network = httpx.AsyncHTTPTransport()

        async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
            requested_urls.append(str(request.url))
            local_request = httpx.Request(
                request.method,
                request.url.copy_with(host="127.0.0.1"),
                headers=request.headers,
                content=request.content,
            )
            return await self._network.handle_async_request(local_request)

        async def aclose(self) -> None:
            await self._network.aclose()

    settings = Settings(
        _env_file=None,
        deployment_environment="rehearsal",
        telegram_bot_api_origin="http://telegram-stub:18080",
    )
    monkeypatch.setattr("core.telegram.gateway.get_settings", lambda: settings)
    client = httpx.AsyncClient(transport=StubNetworkTransport(), trust_env=False)
    gateway = TelegramHTMLGateway(token, http_client=client)
    try:
        await gateway.set_webhook(
            url="https://app.example.test/webhook",
            secret_token="independent_webhook_secret",
        )
        info = await gateway.get_webhook_info()
    finally:
        await client.aclose()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)

    assert info == {"url": "https://app.example.test/webhook"}
    assert methods == ["setWebhook", "getWebhookInfo"]
    assert all(url.startswith("http://telegram-stub:18080/bot") for url in requested_urls)
    assert token not in repr(gateway)


@pytest.mark.asyncio
async def test_set_webhook_accepts_the_generation_bound_url_it_is_always_given() -> None:
    """URL вебхука всегда несёт ?bot_generation=N — запрет query его не касается.

    19.08.2026 ужесточение «никакого query в URL вебхука» уронило шаг деплоя
    configure_telegram_webhook на всех пяти шардах репетиции: bind_webhook_generation
    намеренно привязывает callback-origin Telegram к одному поколению токена в БД,
    и без этой привязки нельзя отличить апдейт от устаревшего поколения. Разрешена
    ровно эта форма — секрет в query по-прежнему отклоняется.
    """
    from core.telegram.webhook_configuration import bind_webhook_generation

    sent: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        sent.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": True})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = TelegramHTMLGateway(
        "123456:abcdefghijklmnopqrstuvwxyz0123456789", http_client=http_client
    )
    url = bind_webhook_generation(
        "https://app.example.test/api/v1/integrations/telegram/webhook", 7
    )

    await gateway.set_webhook(url=url, secret_token="safe-webhook-secret")

    assert sent and sent[0]["url"] == url
    assert sent[0]["url"].endswith("?bot_generation=7")
    await http_client.aclose()
