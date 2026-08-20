from __future__ import annotations

import json
import logging
import uuid
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import grpc
import httpx
import pytest

import apps.api.routers.v1.operator as operator_router
import apps.api.routers.v1.settings_telegram as settings_telegram
import apps.api.routers.v1.settings_vision as settings_vision
import apps.health_watchdog.main as health_watchdog
import core.meta_api.adapters as meta_adapters
import core.meta_api.audit as meta_audit
from core.meta_api.client import MetaApiClient
from core.meta_api.errors import PermanentError, classify_graph_error
from core.meta_api.upload import MediaUploader
from core.operator.queries import task_action_reason
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
from core.vision_runtime import VisionConfigurationError

_REPO_ROOT = Path(__file__).resolve().parents[2]
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


def test_meta_error_drops_raw_graph_and_grpc_messages() -> None:
    graph_error = classify_graph_error(
        190,
        463,
        f"access_token={_SECRET} {_UUID}",
        endpoint="/me",
    )

    class _SecretGrpcError(Exception):
        def code(self):
            return grpc.StatusCode.FAILED_PRECONDITION

        def details(self):
            return f"access_token={_SECRET} {_UUID}"

    grpc_error = MetaApiClient._grpc_to_meta_error(_SecretGrpcError(), endpoint="/me")
    upload_error = MediaUploader._grpc_to_error(_SecretGrpcError(), endpoint="upload")
    upload_response_error = MediaUploader._upload_response_error(
        f"GRAPH_ERROR_190 access_token={_SECRET} {_UUID}",
        endpoint="upload",
    )

    assert str(graph_error) == "Graph API error code=190 subcode=463"
    assert _SECRET not in str(grpc_error)
    assert _UUID not in str(grpc_error)
    assert "FAILED_PRECONDITION" in str(grpc_error)
    assert _SECRET not in str(upload_error)
    assert _UUID not in str(upload_error)
    assert _SECRET not in str(upload_response_error)
    assert _UUID not in str(upload_response_error)


def test_meta_audit_boundary_omits_arbitrary_payload_and_sanitizes_endpoint() -> None:
    payload = meta_audit._safe_audit_payload(  # noqa: SLF001
        {
            "method": "POST",
            "endpoint": f"/me?access_token={_SECRET}",
            "query_keys": ["fields", "access_token"],
            "has_body": True,
            "raw_response": f"Unauthorized token={_SECRET} {_UUID}",
        }
    )

    serialized = json.dumps(payload, ensure_ascii=False)
    assert payload == {
        "method": "POST",
        "endpoint": "/me",
        "query_keys": ["access_token", "fields"],
        "has_body": True,
        "raw_response_omitted": True,
    }
    assert _SECRET not in serialized
    assert _UUID not in serialized


@pytest.mark.asyncio
async def test_meta_audit_records_query_keys_and_error_codes_without_values(monkeypatch) -> None:
    graph_error = PermanentError(
        f"external response access_token={_SECRET}",
        code=190,
        subcode=463,
        endpoint=f"/me?access_token={_SECRET}",
        fbtrace_id=_UUID,
    )
    monkeypatch.setattr(
        MetaApiClient,
        "execute_graph_call",
        AsyncMock(side_effect=graph_error),
    )
    record = AsyncMock()
    monkeypatch.setattr(meta_audit, "record_audit_log", record)
    client = meta_audit.AuditedMetaApiClient(engine=object(), initiated_by="unit")

    with pytest.raises(PermanentError):
        await client.execute_graph_call(
            method="GET",
            endpoint="/me",
            query_params={"fields": "id", "access_token": _SECRET},
        )

    audit_call = record.await_args.kwargs
    assert audit_call["request_payload"]["query_keys"] == ["access_token", "fields"]
    assert audit_call["response_payload"] == {
        "error": {"code": 190, "subcode": 463, "type": "PermanentError"}
    }
    assert _SECRET not in json.dumps(audit_call, default=str)
    assert _UUID not in json.dumps(audit_call, default=str)


def test_meta_adapter_parse_warning_does_not_log_raw_value(caplog) -> None:
    caplog.set_level(logging.WARNING)

    assert meta_adapters._to_decimal(f"access_token={_SECRET}") is None
    assert meta_adapters._to_int(f"access_token={_SECRET}") == 0

    assert "value_type=str" in caplog.text
    assert _SECRET not in caplog.text


def test_operator_incident_copy_redacts_untrusted_text_fields() -> None:
    incident = {
        "id": _UUID,
        "severity": "critical",
        "status": "open",
        "title": f"access_token={_SECRET}",
        "summary": f"external {_UUID}",
        "resource_type": "ad",
        "resource_id": _UUID,
        "resource_label": f"https://tracker.test/ad?token={_SECRET}",
        "ad_account_id": "111",
        "opened_at": health_watchdog.datetime(2026, 8, 15, tzinfo=health_watchdog.UTC),
        "facts": {"risk": f"Bearer {_SECRET}"},
    }

    item = operator_router._incident_item(incident, usd_scope_confirmed=True)

    public_copy = " ".join(
        filter(
            None,
            [item.title, item.summary, item.reason, item.target.label],
        )
    )
    assert _SECRET not in public_copy
    assert _UUID not in public_copy
    assert "<redacted>" in public_copy
    assert item.id.startswith("inc_")
    assert _UUID not in item.id
    assert item.action.href == f"/incidents/{item.id}"


def test_operator_attention_incident_redacts_copy_and_internal_uuid() -> None:
    incident = {
        "id": _UUID,
        "severity": "critical",
        "status": "open",
        "title": f"access_token={_SECRET}",
        "summary": f"external {_UUID}",
        "resource_type": "ad",
        "resource_id": _UUID,
        "resource_label": f"https://tracker.test/ad?token={_SECRET}",
        "opened_at": health_watchdog.datetime(2026, 8, 15, tzinfo=health_watchdog.UTC),
        "incident_key": "autostop:channel_down",
    }

    item = operator_router._incident_attention_item(incident)
    serialized = item.model_dump_json()

    assert item.id.startswith("inc_")
    assert item.action is not None
    assert item.action.href == f"/incidents/{item.id}"
    assert _SECRET not in serialized
    assert _UUID not in serialized


def test_operator_api_sources_do_not_project_raw_correlation_uuid() -> None:
    operator_sources = (
        "apps/api/routers/v1/operator.py",
        "apps/api/routers/v1/settings_observer.py",
        "apps/api/routers/v1/settings_telegram.py",
    )

    for relative_path in operator_sources:
        source = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
        assert "correlation_id=str(" not in source, relative_path


def test_vision_operator_response_never_projects_browser_identity() -> None:
    source = (_REPO_ROOT / "apps/api/routers/v1/settings_vision.py").read_text(encoding="utf-8")

    assert "browser_session_id=probe.browser_session_id" not in source
    assert "live_profile_id=probe.live_profile_id" not in source


def test_vision_public_diagnostics_ignore_raw_probe_and_configuration_details() -> None:
    raw = f"access_token={_SECRET} https://vision.test/probe?token={_SECRET} {_UUID}"

    assert settings_vision._public_probe_failure_message({"probe_detail": raw}) == (
        "Graph probe failed"
    )
    assert (
        settings_vision._public_vision_configuration_message(VisionConfigurationError(raw))
        == "Vision configuration is unavailable"
    )


def test_action_reason_projection_drops_task_internals_from_operator_text() -> None:
    """Причина в снимке действий не несёт traceback, UUID и внутренний код (#206).

    Результат задачи специально враждебный: рядом с операторской причиной лежат
    и машинный код, и traceback, и секрет. Наружу едет только причина, и та —
    санитизированная.
    """
    hostile_result = {
        "outcome": "UNKNOWN",
        "reason": "ack_lost_nothing_confirmed",
        "error": (
            "Traceback (most recent call last):\n"
            '  File "/app/core/campaign_builder/execute.py", line 512, in _create\n'
            f"PermanentError: access_token={_SECRET} {_UUID}"
        ),
        "diagnostics": {
            "exception_class": "PermanentError",
            "code": 100,
            "subcode": 1885316,
            "endpoint": f"https://graph.facebook.example/v23.0/act_1/ads?access_token={_SECRET}",
        },
        "operator_reason": (
            f"Шаг: создание объектов кампании. Ответ Meta: отказ {_UUID} "
            f"fbtrace_id=AbCdEfGhIjKlMnOpQrS access_token={_SECRET}"
        ),
    }

    projected = task_action_reason(hostile_result)

    assert projected is not None
    assert _SECRET not in projected
    assert _UUID not in projected
    assert "AbCdEfGhIjKlMnOpQrS" not in projected
    assert "Traceback" not in projected
    assert "ack_lost_nothing_confirmed" not in projected
    assert "graph.facebook.example" not in projected
    assert "PermanentError" not in projected


def test_action_reason_projection_refuses_a_traceback_shaped_reason() -> None:
    """Причина формы «внутренности Python» не показывается вовсе (#206).

    Обрезанный traceback в карточке оператора хуже честного «причина не
    записана»: он выглядит как объяснение, ничего не объясняя.
    """
    assert (
        task_action_reason(
            {
                "operator_reason": (
                    "Traceback (most recent call last): "
                    '  File "/app/apps/campaign_creator_worker/main.py", line 1204, in run'
                )
            }
        )
        is None
    )


def test_action_reason_is_null_when_no_reason_was_recorded() -> None:
    """Отсутствие причины — null, а не пустая строка и не бодрая константа (#206)."""
    assert task_action_reason({"outcome": "UNKNOWN", "reason": "external_result_ambiguous"}) is None
    assert task_action_reason({"operator_reason": "   "}) is None
    assert task_action_reason(None) is None
