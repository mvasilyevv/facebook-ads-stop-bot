from __future__ import annotations

from contextlib import contextmanager
from types import SimpleNamespace

import pytest

from core import telemetry


class _RecordingSpan:
    def __init__(self) -> None:
        self.attributes: dict[str, str] = {}

    def is_recording(self) -> bool:
        return True

    def set_attribute(self, key: str, value: str) -> None:
        self.attributes[key] = value


def test_http_trace_hook_removes_telegram_token_query_and_userinfo() -> None:
    token = "123456:ABC_super-secret"
    span = _RecordingSpan()
    request = SimpleNamespace(
        url=f"https://user:password@api.telegram.org/bot{token}/sendMessage?capability=raw"
    )

    telemetry._httpx_request_hook(span, request)  # noqa: SLF001

    attributes = repr(span.attributes)
    assert token not in attributes
    assert "password" not in attributes
    assert "capability" not in attributes
    assert span.attributes["url.full"] == ("https://api.telegram.org/bot<redacted>/sendMessage")


def test_server_trace_hook_redacts_query_capabilities() -> None:
    span = _RecordingSpan()
    telemetry._server_request_hook(  # noqa: SLF001
        span,
        {"path": "/api/operator/open", "query_string": b"nav=opaque-secret"},
    )

    assert span.attributes["url.path"] == "/api/operator/open"
    assert span.attributes["url.query"] == "<redacted>"
    assert "opaque-secret" not in repr(span.attributes)


def test_server_trace_hook_redacts_uuid_in_path() -> None:
    raw_uuid = "00000000-0000-4000-8000-000000000099"
    span = _RecordingSpan()

    telemetry._server_request_hook(  # noqa: SLF001
        span,
        {"path": f"/api/operator/incidents/{raw_uuid}", "query_string": b""},
    )

    assert raw_uuid not in repr(span.attributes)
    assert "объект" in span.attributes["url.path"]


@pytest.mark.asyncio
async def test_server_tracing_does_not_record_exception_or_traceback(monkeypatch) -> None:
    class _Span(_RecordingSpan):
        def __init__(self) -> None:
            super().__init__()
            self.statuses: list[object] = []

        def set_status(self, status: object) -> None:
            self.statuses.append(status)

    span = _Span()
    captured_options: dict[str, object] = {}

    class _Tracer:
        @contextmanager
        def start_as_current_span(self, _name: str, **options: object):
            captured_options.update(options)
            yield span

    monkeypatch.setattr(telemetry.trace, "get_tracer", lambda *_args, **_kwargs: _Tracer())

    async def failing_app(_scope, _receive, _send):  # noqa: ANN001
        raise RuntimeError("access_token=must-not-be-recorded")

    middleware = telemetry._SecretSafeTracingMiddleware(  # noqa: SLF001
        failing_app,
        tracer_provider=None,
    )

    with pytest.raises(RuntimeError, match="must-not-be-recorded"):
        await middleware(
            {"type": "http", "method": "GET", "path": "/", "query_string": b""},
            None,  # type: ignore[arg-type]
            None,  # type: ignore[arg-type]
        )

    assert captured_options["record_exception"] is False
    assert captured_options["set_status_on_exception"] is False
    assert "must-not-be-recorded" not in repr(span.statuses)


@pytest.mark.parametrize(
    "endpoint",
    (
        "https://user:password@tempo.example",
        "https://tempo.example/otlp",
        "https://tempo.example?token=secret",
    ),
)
def test_otlp_endpoint_rejects_secret_bearing_urls(endpoint: str) -> None:
    with pytest.raises(ValueError, match="without credentials"):
        telemetry._validated_otlp_endpoint(endpoint)  # noqa: SLF001
