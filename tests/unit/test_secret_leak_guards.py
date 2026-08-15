from __future__ import annotations

import ast
import json
import logging
import uuid
from base64 import urlsafe_b64encode
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import grpc
import httpx
import pytest
from fastapi import UploadFile

import apps.api.routers.postback as postback_router
import apps.api.routers.v1.operator as operator_router
import apps.api.routers.v1.settings_telegram as settings_telegram
import apps.api.routers.v1.settings_vision as settings_vision
import apps.api.routers.v1.tools as tools_router
import apps.campaign_creator_worker.main as campaign_creator_worker
import apps.health_watchdog.main as health_watchdog
import core.ai_assistant.diagnostics as ai_diagnostics
import core.ai_assistant.providers as providers_module
import core.creatives.folder_opener as folder_opener
import core.meta_api.adapters as meta_adapters
import core.meta_api.audit as meta_audit
import core.observer.writers as observer_writers
import core.telegram.worker_notify as worker_notify
from core.adset_duplicates.service import DuplicateTask, serialize_duplicate_task
from core.adset_pro.client import AdsetProClient
from core.adset_pro.ingest import IngestResult
from core.ai_assistant.providers import AIResponse, AnthropicProvider, ProviderError
from core.meta_api.client import MetaApiClient
from core.meta_api.errors import PermanentError, classify_graph_error
from core.meta_api.upload import MediaUploader
from core.public_identifiers import parse_public_uuid, public_uuid
from core.safe_diagnostics import redact_sensitive_text, safe_exception_diagnostic
from core.syntx.client import SyntxClient
from core.syntx.errors import SyntxError
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


class _FakeHTTPClient:
    def __init__(self, response: httpx.Response) -> None:
        self._response = response

    async def __aenter__(self) -> _FakeHTTPClient:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def post(self, *_args: object, **_kwargs: object) -> httpx.Response:
        return self._response


@pytest.mark.asyncio
async def test_provider_error_does_not_expose_external_response_body(monkeypatch) -> None:
    response = httpx.Response(
        400,
        content=f'{{"error":"access_token={_SECRET}"}}'.encode(),
        request=httpx.Request("POST", "https://gateway.test/messages"),
    )
    monkeypatch.setattr(
        providers_module.httpx,
        "AsyncClient",
        lambda **_kwargs: _FakeHTTPClient(response),
    )
    provider = AnthropicProvider(
        api_key="provider-secret",
        base_url="https://gateway.test",
        model="primary-model",
    )

    with pytest.raises(ProviderError) as error:
        await provider.chat(messages=[{"role": "user", "content": "hi"}])

    assert str(error.value) == "anthropic: HTTP 400"
    assert _SECRET not in str(error.value)


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


def test_public_uuid_is_opaque_round_trip_and_raw_uuid_is_not_accepted_as_public() -> None:
    internal = uuid.UUID(_UUID)

    public = public_uuid(internal, prefix="inc")

    assert public.startswith("inc_")
    assert _UUID not in public
    assert parse_public_uuid(public, prefix="inc") == internal
    with pytest.raises(ValueError, match="invalid public identifier"):
        parse_public_uuid(_UUID, prefix="inc")


def test_campaign_worker_log_handle_never_contains_run_uuid() -> None:
    assert campaign_creator_worker._public_run_log_id(_UUID).startswith("run_")
    assert _UUID not in campaign_creator_worker._public_run_log_id(_UUID)
    assert campaign_creator_worker._public_run_log_id(f"token={_SECRET}") == "run_invalid"


@pytest.mark.asyncio
async def test_postback_response_does_not_echo_provider_or_internal_ids(monkeypatch) -> None:
    monkeypatch.setattr(
        postback_router,
        "ingest_postback",
        AsyncMock(
            return_value=IngestResult(
                inserted=True,
                is_duplicate=False,
                event_id=123,
                fb_ad_fk=uuid.UUID(_UUID),
                attribution_status="matched",
                task_id=456,
            )
        ),
    )

    response = await postback_router._handle(
        payload={"event": "ftd", "click_id": f"click-{_SECRET}"},
        engine=object(),
        redis=None,
        accepted_status=200,
    )
    content = response.body.decode("utf-8")

    assert json.loads(content) == {
        "received": True,
        "status": "accepted",
        "inserted": True,
        "is_duplicate": False,
    }
    assert _SECRET not in content
    assert _UUID not in content


def test_campaign_tool_paths_do_not_expose_server_root_or_secret_filename() -> None:
    public_path = tools_router._public_creative_path(
        "/srv/private/FB_Agent_Creo/offer",
        "/srv/private/FB_Agent_Creo/offer/1",
        f"/srv/private/FB_Agent_Creo/offer/1/access_token={_SECRET}.png",
    )

    assert public_path.startswith("offer/1/")
    assert "/srv/private" not in public_path
    assert _SECRET not in public_path
    assert "<redacted>" in public_path


def test_duplicate_status_never_exposes_worker_last_error() -> None:
    now = health_watchdog.datetime(2026, 8, 15, tzinfo=health_watchdog.UTC)
    task = DuplicateTask(
        id=77,
        status="failed",
        payload={"params": {"counts": {"total_objects": 1}}},
        result={"outcome": "REJECTED", "reason": "worker_failure"},
        attempt_count=1,
        max_attempts=1,
        last_error=f"Traceback access_token={_SECRET} {_UUID}",
        created_at=now,
        updated_at=now,
        completed_at=now,
    )

    serialized = serialize_duplicate_task(task)

    assert serialized["error"] == (
        "Дублирование завершилось ошибкой. Проверьте состояние в Meta перед повтором."
    )
    assert _SECRET not in json.dumps(serialized, ensure_ascii=False)
    assert _UUID not in json.dumps(serialized, ensure_ascii=False)


def test_vision_public_diagnostics_ignore_raw_probe_and_configuration_details() -> None:
    raw = f"access_token={_SECRET} https://vision.test/probe?token={_SECRET} {_UUID}"

    assert settings_vision._public_probe_failure_message({"probe_detail": raw}) == (
        "Graph probe failed"
    )
    assert (
        settings_vision._public_vision_configuration_message(VisionConfigurationError(raw))
        == "Vision configuration is unavailable"
    )


@pytest.mark.asyncio
async def test_ai_diagnostics_redacts_log_input_and_provider_output(monkeypatch) -> None:
    settings = SimpleNamespace(
        ai_diagnostics_enabled=True,
        ai_diagnostics_cooldown_seconds=0,
        ai_max_log_lines=20,
        ai_timeout_seconds=1,
    )
    client = SimpleNamespace(
        is_available=True,
        chat=AsyncMock(return_value=AIResponse(text=f"diagnosis token={_SECRET} {_UUID}")),
    )
    monkeypatch.setattr(ai_diagnostics, "get_settings", lambda: settings)
    monkeypatch.setattr(ai_diagnostics, "get_ai_client", lambda _settings: client)
    monkeypatch.setattr(
        ai_diagnostics,
        "_read_log_tail",
        lambda *_args: f"Authorization: Bearer {_SECRET}\nurl=https://x.test/?token={_SECRET}",
    )
    ai_diagnostics.reset_diagnose_cooldown_for_tests()

    result = await ai_diagnostics.diagnose_alert(
        alert_key=f"incident:{_UUID}",
        context=f"access_token={_SECRET}",
    )

    outbound = client.chat.await_args.kwargs["messages"][0]["content"]
    assert _SECRET not in outbound
    assert _UUID not in outbound
    assert _SECRET not in result
    assert _UUID not in result


@pytest.mark.asyncio
async def test_ai_diagnostics_timeout_log_redacts_alert_key(monkeypatch, caplog) -> None:
    settings = SimpleNamespace(
        ai_diagnostics_enabled=True,
        ai_diagnostics_cooldown_seconds=0,
        ai_max_log_lines=20,
        ai_timeout_seconds=1,
    )
    client = SimpleNamespace(is_available=True, chat=AsyncMock(side_effect=TimeoutError))
    monkeypatch.setattr(ai_diagnostics, "get_settings", lambda: settings)
    monkeypatch.setattr(ai_diagnostics, "get_ai_client", lambda _settings: client)
    monkeypatch.setattr(ai_diagnostics, "_read_log_tail", lambda *_args: "safe log")
    ai_diagnostics.reset_diagnose_cooldown_for_tests()
    caplog.set_level(logging.WARNING)

    result = await ai_diagnostics.diagnose_alert(alert_key=f"token={_SECRET}")

    assert result is None
    assert "AI diagnose: timeout" in caplog.text
    assert _SECRET not in caplog.text


def test_meta_adapter_parse_warning_does_not_log_raw_value(caplog) -> None:
    caplog.set_level(logging.WARNING)

    assert meta_adapters._to_decimal(f"access_token={_SECRET}") is None
    assert meta_adapters._to_int(f"access_token={_SECRET}") == 0

    assert "value_type=str" in caplog.text
    assert _SECRET not in caplog.text


@pytest.mark.asyncio
async def test_external_client_start_logs_strip_url_credentials_and_query(caplog) -> None:
    jwt_payload = urlsafe_b64encode(b'{"exp":4102444800}').decode().rstrip("=")
    base_url = f"https://user:{_SECRET}@gateway.test/api?token={_SECRET}"
    adset_client = AdsetProClient(api_key="configured", base_url=base_url)
    syntx_client = SyntxClient(token=f"x.{jwt_payload}.x", base_url=base_url)
    caplog.set_level(logging.INFO)

    await adset_client.start()
    await syntx_client.start()
    await adset_client.close()
    await syntx_client.close()

    assert "gateway.test/api" in caplog.text
    assert "user" not in caplog.text
    assert _SECRET not in caplog.text


@pytest.mark.asyncio
async def test_autostop_card_uses_affected_count_instead_of_meta_ids(monkeypatch) -> None:
    monkeypatch.setattr(
        health_watchdog,
        "query_stuck_pause_tasks",
        AsyncMock(return_value=[SimpleNamespace(target_id="238500000000001")]),
    )
    monkeypatch.setattr(
        health_watchdog,
        "query_desynced_stop_ads",
        AsyncMock(return_value=[SimpleNamespace(fb_ad_id="238500000000002")]),
    )
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(health_watchdog, "_enqueue_critical_notification", notify)

    assert await health_watchdog.check_autostop_channel(object()) is True

    lines = notify.await_args.kwargs["lines"]
    assert lines[0] == "Затронуто объявлений: 2"
    assert "238500000000001" not in " ".join(lines)
    assert "238500000000002" not in " ".join(lines)


def test_logs_never_enable_tracebacks() -> None:
    paths = sorted(path for root in ("core", "apps") for path in (_REPO_ROOT / root).rglob("*.py"))

    for path in paths:
        source = path.read_text(encoding="utf-8")
        assert "logger.exception(" not in source, path.relative_to(_REPO_ROOT)
        assert "exc_info=True" not in source, path.relative_to(_REPO_ROOT)


def test_loggers_never_receive_raw_exception_or_persisted_error() -> None:
    paths = sorted(path for root in ("core", "apps") for path in (_REPO_ROOT / root).rglob("*.py"))

    for path in paths:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for call in (node for node in ast.walk(tree) if isinstance(node, ast.Call)):
            if not (
                isinstance(call.func, ast.Attribute)
                and isinstance(call.func.value, ast.Name)
                and call.func.value.id == "logger"
            ):
                continue
            for argument in call.args[1:]:
                raw_name = isinstance(argument, ast.Name) and argument.id in {"exc", "last_error"}
                raw_attribute = isinstance(argument, ast.Attribute) and argument.attr in {
                    "last_error",
                    "response_body",
                }
                assert not (raw_name or raw_attribute), (
                    f"raw diagnostic passed to logger at {path.relative_to(_REPO_ROOT)}:"
                    f"{call.lineno}"
                )


def test_external_boundary_loggers_have_no_raw_exception_formatting() -> None:
    relative_paths = (
        "apps/meta_api_worker/main.py",
        "apps/campaign_creator_worker/main.py",
        "apps/health_watchdog/main.py",
        "core/ai_assistant/client.py",
        "core/ai_assistant/providers.py",
        "core/ai_assistant/tools/registry.py",
        "core/meta_api/client.py",
        "core/meta_api/mutations/duplicate_adset_structure.py",
        "core/meta_api/upload.py",
        "core/telegram/menu_button.py",
        "core/telegram/settings_compute.py",
        "core/telegram/handlers/alerts.py",
        "core/telegram/handlers/router.py",
    )

    for relative_path in relative_paths:
        source = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
        assert "repr(exc)" not in source, relative_path
        assert "{exc!r}" not in source, relative_path
        if relative_path in {"core/meta_api/client.py", "core/meta_api/upload.py"}:
            assert "exc.details()" not in source, relative_path


def test_vision_operator_response_never_projects_browser_identity() -> None:
    source = (_REPO_ROOT / "apps/api/routers/v1/settings_vision.py").read_text(encoding="utf-8")

    assert "browser_session_id=probe.browser_session_id" not in source
    assert "live_profile_id=probe.live_profile_id" not in source


def test_operator_api_sources_do_not_project_raw_correlation_uuid() -> None:
    operator_sources = (
        "apps/api/routers/v1/operator.py",
        "apps/api/routers/v1/settings_observer.py",
        "apps/api/routers/v1/settings_telegram.py",
    )

    for relative_path in operator_sources:
        source = (_REPO_ROOT / relative_path).read_text(encoding="utf-8")
        assert "correlation_id=str(" not in source, relative_path
        assert "correlation_id=receipt.correlation_id" not in source, relative_path


def test_safe_error_logs_do_not_include_secret(caplog) -> None:
    caplog.set_level(logging.WARNING)
    exc = RuntimeError(f"token={_SECRET}")

    logging.getLogger("secret-guard").warning(
        "external call failed (%s)",
        safe_exception_diagnostic(exc),
    )

    assert "external call failed" in caplog.text
    assert "RuntimeError" in caplog.text
    assert _SECRET not in caplog.text


@pytest.mark.asyncio
async def test_observer_metric_failure_log_uses_opaque_ad_id_and_safe_error(caplog) -> None:
    class _ExplodingContext:
        async def __aenter__(self):
            raise RuntimeError(f"token={_SECRET} {_UUID}")

        async def __aexit__(self, *_args: object) -> None:
            return None

    class _ExplodingEngine:
        def begin(self) -> _ExplodingContext:
            return _ExplodingContext()

    with caplog.at_level(logging.WARNING, logger=observer_writers.__name__):
        inserted = await observer_writers.insert_metrics(
            _ExplodingEngine(),  # type: ignore[arg-type]
            ad_id=uuid.UUID(_UUID),
            cycle_ts=datetime.now(UTC),
            scan_id=1,
            currency="USD",
            metrics={},
        )

    assert inserted is False
    assert "ad_" in caplog.text
    assert "error_type=RuntimeError" in caplog.text
    assert _UUID not in caplog.text
    assert _SECRET not in caplog.text


@pytest.mark.asyncio
async def test_syntx_analysis_error_drops_provider_message_and_chat_uuid() -> None:
    client = SyntxClient(token="unit-token")
    client.create_chat = AsyncMock(return_value=_UUID)  # type: ignore[method-assign]
    client._post_text_message = AsyncMock(  # type: ignore[method-assign]
        side_effect=SyntxError(
            f"provider response token={_SECRET} chat={_UUID}",
            status_code=503,
            response_body=f'{{"token":"{_SECRET}"}}',
        )
    )
    client.delete_chat = AsyncMock(return_value=True)  # type: ignore[method-assign]

    results = await client.analyze_ensemble(
        None,
        "analyze",
        models=[("model-provider", "model-version", "Model")],
    )

    assert results[0].error == "Syntx analysis failed (error_type=SyntxError status=503)"
    assert _SECRET not in results[0].error
    assert _UUID not in results[0].error


@pytest.mark.asyncio
async def test_creative_uniquify_response_does_not_expose_server_path(monkeypatch) -> None:
    monkeypatch.setattr(
        tools_router,
        "uniquify_creatives",
        AsyncMock(
            return_value=SimpleNamespace(
                iteration_dir="/srv/private/FB_Agent_Creo/safe-batch",
                iteration_name="safe-batch",
                files=["one.jpeg"],
                creative_count=1,
                copy_count=1,
            )
        ),
    )
    upload = UploadFile(file=BytesIO(b"image"), filename="creative.png")

    response = await tools_router.creative_uniquify(
        offer_name="Offer",
        copies=1,
        files=[upload],
        _=None,
    )

    assert response.output_dir == "safe-batch"
    assert "/srv/private" not in response.model_dump_json()


@pytest.mark.asyncio
async def test_campaign_folder_list_replaces_raw_validation_error(monkeypatch) -> None:
    monkeypatch.setattr(
        tools_router,
        "list_creative_folders",
        AsyncMock(
            return_value=[
                SimpleNamespace(
                    name="safe-batch",
                    path="/srv/private/FB_Agent_Creo/safe-batch",
                    adset_count=0,
                    creative_count=0,
                    media_type="unknown",
                    updated_at=0.0,
                    is_valid=False,
                    validation_error=f"ffmpeg at /srv/private token={_SECRET} {_UUID}",
                )
            ]
        ),
    )

    response = await tools_router.get_campaign_creative_folders()
    serialized = response[0].model_dump_json()

    assert response[0].validation_error == "Папка не прошла валидацию"
    assert "/srv/private" not in serialized
    assert _SECRET not in serialized
    assert _UUID not in serialized


@pytest.mark.asyncio
async def test_open_folder_resolves_public_relative_path_inside_creative_root(
    monkeypatch,
    tmp_path,
) -> None:
    target = tmp_path / "safe-batch"
    target.mkdir()
    process = SimpleNamespace(communicate=AsyncMock(), returncode=0)
    create_process = AsyncMock(return_value=process)
    monkeypatch.setattr(folder_opener.asyncio, "create_subprocess_exec", create_process)
    monkeypatch.setattr(folder_opener.sys, "platform", "darwin")

    await folder_opener.open_generated_folder("safe-batch", base_dir=tmp_path)

    create_process.assert_awaited_once_with(
        "open",
        str(target),
        stdout=folder_opener.asyncio.subprocess.DEVNULL,
        stderr=folder_opener.asyncio.subprocess.DEVNULL,
    )


@pytest.mark.asyncio
async def test_recurring_incident_failure_log_redacts_incident_key(caplog) -> None:
    class _ExplodingContext:
        async def __aenter__(self):
            raise RuntimeError(f"token={_SECRET}")

        async def __aexit__(self, *_args: object) -> None:
            return None

    class _ExplodingEngine:
        def begin(self) -> _ExplodingContext:
            return _ExplodingContext()

    with caplog.at_level(logging.ERROR, logger=worker_notify.__name__):
        accepted = await worker_notify.notify_recurring_incident(
            _ExplodingEngine(),  # type: ignore[arg-type]
            incident_key=f"incident:{_UUID}:token={_SECRET}",
            audience="all",
            event_type="unit_failure",
            severity="critical",
            title="Failure",
        )

    assert accepted is False
    assert "recurring incident enqueue failed" in caplog.text
    assert _UUID not in caplog.text
    assert _SECRET not in caplog.text
