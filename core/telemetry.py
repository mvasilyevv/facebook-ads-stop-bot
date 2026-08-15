"""OpenTelemetry bootstrap with secret-safe HTTP attributes.

Applications export OTLP/gRPC to host-local Alloy. Alloy owns off-host
credentials and forwarding to Tempo; telemetry failures never own readiness.
"""

from __future__ import annotations

import atexit
import logging
import os
import re
from urllib.parse import urlsplit, urlunsplit

from fastapi import FastAPI
from opentelemetry import trace
from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import OTLPSpanExporter
from opentelemetry.sdk.resources import DEPLOYMENT_ENVIRONMENT, SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Span, SpanKind, Status, StatusCode
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from core.safe_diagnostics import redact_sensitive_text

logger = logging.getLogger(__name__)

_configured = False
_provider: TracerProvider | None = None
_TELEGRAM_BOT_PATH = re.compile(r"/bot[^/]+", re.IGNORECASE)


def sanitized_http_url(raw_url: object) -> str:
    """Return a trace-safe URL without userinfo, query, fragment or bot token."""
    try:
        parsed = urlsplit(str(raw_url))
        hostname = parsed.hostname or ""
        port = f":{parsed.port}" if parsed.port is not None else ""
        path = _TELEGRAM_BOT_PATH.sub("/bot<redacted>", parsed.path)
        return urlunsplit((parsed.scheme, f"{hostname}{port}", path, "", ""))
    except (TypeError, ValueError):
        return "<invalid-url>"


def _validated_otlp_endpoint(raw_endpoint: str) -> str:
    parsed = urlsplit(raw_endpoint)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
        or parsed.path not in {"", "/"}
    ):
        raise ValueError("OTLP endpoint must be an HTTP(S) origin without credentials")
    return raw_endpoint.rstrip("/")


def _service_name() -> str:
    return (
        os.getenv("OTEL_SERVICE_NAME")
        or os.getenv("WORKER_TYPE")
        or os.getenv("META_API_WORKER_NAME")
        or "fb-agent"
    )


def _httpx_request_hook(span: Span, request: object) -> None:
    if not span.is_recording():
        return
    safe_url = sanitized_http_url(getattr(request, "url", ""))
    span.set_attribute("url.full", safe_url)
    span.set_attribute("http.url", safe_url)


def _server_request_hook(span: Span, scope: dict[str, object]) -> None:
    if not span.is_recording():
        return
    path = redact_sensitive_text(scope.get("path") or "/")
    span.set_attribute("url.path", path)
    span.set_attribute("http.target", path)
    if scope.get("query_string"):
        span.set_attribute("url.query", "<redacted>")


def initialize_telemetry() -> bool:
    """Configure process-wide SDK/instrumentation when an endpoint is present."""
    global _configured, _provider
    if _configured:
        return True
    raw_endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not raw_endpoint:
        return False
    endpoint = _validated_otlp_endpoint(raw_endpoint)
    try:
        sampling_ratio = float(os.getenv("OTEL_TRACES_SAMPLER_ARG", "0.10"))
    except ValueError:
        sampling_ratio = 0.10
    sampling_ratio = min(1.0, max(0.0, sampling_ratio))
    resource = Resource.create(
        {
            SERVICE_NAME: _service_name(),
            DEPLOYMENT_ENVIRONMENT: os.getenv("DEPLOYMENT_ENVIRONMENT", "production"),
            "service.version": os.getenv("FB_AGENT_RELEASE", "unknown"),
            "deployment.color": os.getenv("FB_AGENT_DEPLOYMENT_COLOR", "unknown"),
        }
    )
    provider = TracerProvider(
        resource=resource,
        sampler=ParentBased(TraceIdRatioBased(sampling_ratio)),
    )
    provider.add_span_processor(
        BatchSpanProcessor(
            OTLPSpanExporter(
                endpoint=endpoint,
                insecure=urlsplit(endpoint).scheme == "http",
            )
        )
    )
    trace.set_tracer_provider(provider)
    _provider = provider
    _configured = True
    atexit.register(shutdown_telemetry)
    logger.info(
        "OpenTelemetry enabled service=%s collector=%s",
        _service_name(),
        sanitized_http_url(endpoint),
    )
    return True


class _SecretSafeTracingMiddleware:
    """Create bounded HTTP spans without exception events, bodies or headers."""

    def __init__(self, app: ASGIApp, *, tracer_provider: TracerProvider | None) -> None:
        self.app = app
        self.tracer = trace.get_tracer(__name__, tracer_provider=tracer_provider)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        method = str(scope.get("method") or "HTTP")[:16]
        status_code: int | None = None

        async def safe_send(message: Message) -> None:
            nonlocal status_code
            if message.get("type") == "http.response.start":
                status_code = int(message.get("status") or 0)
            await send(message)

        with self.tracer.start_as_current_span(
            f"HTTP {method}",
            kind=SpanKind.SERVER,
            record_exception=False,
            set_status_on_exception=False,
        ) as span:
            _server_request_hook(span, scope)
            try:
                await self.app(scope, receive, safe_send)
            except Exception as exc:  # noqa: BLE001 - status retains only the safe class name
                span.set_status(Status(StatusCode.ERROR, f"error_type={type(exc).__name__}"))
                raise
            if status_code is not None:
                span.set_attribute("http.response.status_code", status_code)
                if status_code >= 500:
                    span.set_status(Status(StatusCode.ERROR, f"HTTP {status_code}"))


def instrument_fastapi(app: FastAPI) -> bool:
    """Attach secret-safe server spans after routers and middleware are registered."""
    if not _configured or getattr(app.state, "otel_instrumented", False):
        return False
    app.add_middleware(_SecretSafeTracingMiddleware, tracer_provider=_provider)
    app.state.otel_instrumented = True
    return True


def shutdown_telemetry() -> None:
    global _provider
    provider = _provider
    _provider = None
    if provider is not None:
        provider.shutdown()


__all__ = [
    "initialize_telemetry",
    "instrument_fastapi",
    "sanitized_http_url",
    "shutdown_telemetry",
]
