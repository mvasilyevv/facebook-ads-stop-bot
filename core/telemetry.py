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
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor
from opentelemetry.instrumentation.grpc import (
    GrpcAioInstrumentorClient,
    GrpcAioInstrumentorServer,
)
from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor
from opentelemetry.sdk.resources import DEPLOYMENT_ENVIRONMENT, SERVICE_NAME, Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor
from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased
from opentelemetry.trace import Span

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
    path = str(scope.get("path") or "/")
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
    HTTPXClientInstrumentor().instrument(
        tracer_provider=provider,
        request_hook=_httpx_request_hook,
    )
    SQLAlchemyInstrumentor().instrument(tracer_provider=provider)
    GrpcAioInstrumentorClient().instrument(tracer_provider=provider)
    GrpcAioInstrumentorServer().instrument(tracer_provider=provider)
    _provider = provider
    _configured = True
    atexit.register(shutdown_telemetry)
    logger.info(
        "OpenTelemetry enabled service=%s collector=%s",
        _service_name(),
        sanitized_http_url(endpoint),
    )
    return True


def instrument_fastapi(app: FastAPI) -> bool:
    """Attach server spans after routers and middleware have been registered."""
    if not _configured or getattr(app.state, "otel_instrumented", False):
        return False
    FastAPIInstrumentor.instrument_app(
        app,
        tracer_provider=_provider,
        excluded_urls="healthz,readyz,system-readyz,metrics",
        server_request_hook=_server_request_hook,
    )
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
