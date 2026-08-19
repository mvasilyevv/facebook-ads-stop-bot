from __future__ import annotations

import json
import uuid
from types import SimpleNamespace

import httpx
import pytest

import apps.api.routers.v1.settings_telegram as settings_telegram
from core.public_identifiers import parse_public_uuid, public_uuid
from core.safe_diagnostics import redact_sensitive_text, safe_exception_diagnostic
from core.telegram.command_replies import DurableTelegramUpdateClient
from core.telegram.gateway import TelegramHTMLGateway
from core.telegram.notification_renderer import render_notification
from core.telegram.schemas import (
    NotificationActionSpec,
    NotificationCardFacts,
    NotificationEventSpec,
)
from core.telegram.web_app_url import normalize_web_app_base
from core.telemetry import sanitized_http_url

_SECRET = "123456789:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdef"
_UUID = "00000000-0000-4000-8000-000000000099"


class _CodedSecretError(RuntimeError):
    code = 190
    subcode = 463


def test_safe_diagnostic_never_formats_exception_message_or_traceback() -> None:
    exc = _CodedSecretError(
        f"access_token={_SECRET} https://tracker.test/cb?token={_SECRET} {_UUID}"
    )

    diagnostic = safe_exception_diagnostic(exc)

    assert diagnostic == "error_type=_CodedSecretError code=190 subcode=463"
    assert _SECRET not in diagnostic
    assert _UUID not in diagnostic
    assert "Traceback" not in diagnostic


def test_public_text_redactor_covers_credentials_capabilities_queries_and_uuid() -> None:
    value = (
        f"Bearer {_SECRET} token={_SECRET} {_SECRET} a:{'A' * 22} "
        f"https://tracker.test/cb?email=user@example.test&token={_SECRET} {_UUID}"
    )

    redacted = redact_sensitive_text(value)

    assert _SECRET not in redacted
    assert _UUID not in redacted
    assert "user@example.test" not in redacted
    assert "A" * 22 not in redacted
    assert "<redacted>" in redacted
    assert "объект" in redacted


def test_public_uuid_is_opaque_round_trip_and_raw_uuid_is_not_accepted_as_public() -> None:
    internal = uuid.UUID(_UUID)

    public = public_uuid(internal, prefix="inc")

    assert public.startswith("inc_")
    assert _UUID not in public
    assert parse_public_uuid(public, prefix="inc") == internal
    with pytest.raises(ValueError, match="invalid public identifier"):
        parse_public_uuid(_UUID, prefix="inc")


def _telegram_event(*, label: str) -> NotificationEventSpec:
    return NotificationEventSpec(
        event_type="incident.opened",
        severity="critical",
        facts=NotificationCardFacts(
            title=f"token={_SECRET}",
            summary=f"raw {_UUID}",
            risk=f"https://tracker.test/cb?token={_SECRET}",
        ),
        actions=[
            NotificationActionSpec(
                key="pause",
                label=label,
                kind="pause_ad",
                target_type="fb_ad",
                target_id="123",
            )
        ],
        dedupe_key="incident:secret-guard",
    )


def test_telegram_renderer_redacts_facts_and_button_label() -> None:
    rendered = render_notification(
        _telegram_event(label="a:" + "B" * 22),
        action_callbacks={"pause": "a:" + "A" * 22},
    )
    serialized = json.dumps(
        {"text": rendered.text, "reply_markup": rendered.reply_markup},
        ensure_ascii=False,
    )

    assert _SECRET not in serialized
    assert _UUID not in serialized
    assert "B" * 22 not in serialized
    assert "tracker.test/cb?&lt;redacted&gt;" in serialized
    assert "объект" in serialized


@pytest.mark.asyncio
async def test_telegram_gateway_blocks_safe_line_bypass_for_text_and_labels() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"ok": True, "result": {"message_id": 42}})

    http_client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    gateway = TelegramHTMLGateway(_SECRET, http_client=http_client)
    durable_client = DurableTelegramUpdateClient(SimpleNamespace())  # type: ignore[arg-type]

    await durable_client.send_message(chat_id=7, text=f"token={_SECRET} {_UUID}")
    await gateway.send_message(
        chat_id=7,
        text=f"token={_SECRET} {_UUID}",
        reply_markup={
            "inline_keyboard": [[{"text": f"token={_SECRET}", "callback_data": "a:" + "A" * 22}]]
        },
    )

    assert _SECRET not in durable_client.replies[0].text
    assert _UUID not in durable_client.replies[0].text
    assert _SECRET.encode() not in requests[0].content
    assert _UUID.encode() not in requests[0].content
    await http_client.aclose()


@pytest.mark.asyncio
async def test_telegram_gateway_rejects_webhook_secret_query() -> None:
    http_client = httpx.AsyncClient(transport=httpx.MockTransport(lambda _request: None))
    gateway = TelegramHTMLGateway(_SECRET, http_client=http_client)

    with pytest.raises(ValueError, match="without credentials or query"):
        await gateway.set_webhook(
            url=f"https://app.test/webhook?token={_SECRET}",
            secret_token="safe-webhook-secret",
        )

    await http_client.aclose()


@pytest.mark.parametrize(
    "navigation_url",
    [
        "https://app.test/tma?nav=" + "N" * 22 + "&token=secret",
        "https://app.test/tma?nav=" + "N" * 22 + "#fragment",
        "https://user:password@app.test/tma?nav=" + "N" * 22,
        "https://app.test/tma?nav=" + "N" * 22 + "%20",
    ],
)
def test_telegram_renderer_rejects_navigation_url_side_channels(navigation_url: str) -> None:
    with pytest.raises(ValueError, match="Invalid opaque Telegram navigation URL"):
        render_notification(_telegram_event(label="Pause"), navigation_url=navigation_url)


@pytest.mark.parametrize(
    "raw_url",
    [
        "https://app.test/tma?token=secret",
        "https://app.test/tma#access_token=secret",
        "https://user:password@app.test/tma",
        "https://app.test/tma path",
    ],
)
def test_web_app_base_rejects_url_credentials_and_history_leaks(raw_url: str) -> None:
    assert normalize_web_app_base(raw_url) is None


def test_telegram_diagnostics_do_not_return_remote_error_or_url_query() -> None:
    raw = f"Unauthorized access_token={_SECRET} at https://api.test/hook?token={_SECRET}"

    assert settings_telegram._public_telegram_diagnostic_message(raw) == (
        "Telegram отклонил bot token"
    )
    assert sanitized_http_url(f"https://api.telegram.org/bot{_SECRET}/hook?token={_SECRET}") == (
        "https://api.telegram.org/bot<redacted>/hook"
    )
