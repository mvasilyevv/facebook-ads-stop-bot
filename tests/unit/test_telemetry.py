from __future__ import annotations

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
