"""Normalize every HTTP error response to the canonical ``ApiProblem`` shape."""

from __future__ import annotations

import json
import re
import uuid
from http import HTTPStatus
from typing import Any

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from apps.api.schemas.problem import ApiProblem

_REQUEST_ID_HEADER = b"x-request-id"
_SAFE_REQUEST_ID = re.compile(r"^[A-Za-z0-9._:-]{1,128}$")
_PROBLEM_FIELDS = frozenset({"code", "message", "correlation_id", "field_errors"})
# Readiness and metrics endpoints are machine-readable probe protocols, not
# product API responses. Their non-2xx bodies intentionally carry blockers and
# Prometheus diagnostics consumed by deployment/monitoring tooling.
NON_API_PROBE_PATHS = frozenset(
    {
        "/healthz",
        "/readyz",
        "/system-readyz",
        "/desktop-readyz",
        "/desktop-kasm-readyz",
        "/metrics",
    }
)
# These are browser-facing HTML handshakes, not JSON API resources. Preserving
# their native error pages is part of the login UX and avoids replacing an
# actionable explanation with a generic JSON document.
NON_API_HTML_PREFIXES = ("/auth/", "/desktop-auth/")
_STATUS_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    412: "precondition_failed",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "unprocessable_entity",
    429: "rate_limited",
    500: "internal_error",
    502: "upstream_error",
    503: "service_unavailable",
    504: "gateway_timeout",
}
_PRIVATE_STATUS_MESSAGES = {
    500: "Внутренняя ошибка сервиса",
    502: "Внешний сервис вернул ошибку",
    503: "Сервис временно недоступен",
    504: "Внешний сервис не ответил вовремя",
}


def request_correlation_id(scope: Scope) -> str:
    """Return one bounded request id from state/header, or generate a new one."""

    state = scope.setdefault("state", {})
    state_value = state.get("request_id")
    if isinstance(state_value, str) and _SAFE_REQUEST_ID.fullmatch(state_value):
        return state_value

    for key, raw_value in scope.get("headers") or ():
        if key.lower() != _REQUEST_ID_HEADER:
            continue
        try:
            header_value = raw_value.decode("ascii")
        except UnicodeDecodeError:
            break
        if _SAFE_REQUEST_ID.fullmatch(header_value):
            state["request_id"] = header_value
            return header_value
        break

    generated = uuid.uuid4().hex
    state["request_id"] = generated
    return generated


def api_problem_payload(
    *,
    code: str,
    message: str,
    correlation_id: str,
    field_errors: dict[str, list[str]] | None = None,
) -> dict[str, Any]:
    """Serialize the required four-field contract without optional omissions."""

    return ApiProblem(
        code=code,
        message=message,
        correlation_id=correlation_id,
        field_errors=field_errors,
    ).model_dump(mode="json")


def default_problem_code(status_code: int) -> str:
    return _STATUS_CODES.get(status_code, f"http_{status_code}")


def default_problem_message(status_code: int) -> str:
    if status_code in _PRIVATE_STATUS_MESSAGES:
        return _PRIVATE_STATUS_MESSAGES[status_code]
    try:
        return HTTPStatus(status_code).phrase
    except ValueError:
        return "HTTP request failed"


def _normalized_field_errors(value: object) -> dict[str, list[str]] | None:
    if not isinstance(value, dict):
        return None
    normalized: dict[str, list[str]] = {}
    for raw_field, raw_messages in value.items():
        if not isinstance(raw_field, str) or not isinstance(raw_messages, list):
            continue
        messages = [
            message[:512] for message in raw_messages if isinstance(message, str) and message
        ]
        if messages:
            normalized[raw_field[:256]] = messages
    return normalized or None


def _decode_error_body(body: bytes) -> dict[str, Any] | None:
    try:
        decoded = json.loads(body)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    return decoded if isinstance(decoded, dict) else None


def _problem_from_response(
    *,
    status_code: int,
    body: bytes,
    correlation_id: str,
) -> bytes:
    payload = _decode_error_body(body)
    is_canonical = payload is not None and _PROBLEM_FIELDS.issubset(payload)

    raw_code = payload.get("code") if payload else None
    if not isinstance(raw_code, str) or not raw_code:
        raw_kind = payload.get("kind") if payload else None
        raw_code = raw_kind if isinstance(raw_kind, str) and raw_kind else None
    code = (raw_code or default_problem_code(status_code))[:128]

    raw_message = payload.get("message") if payload else None
    if not isinstance(raw_message, str) or not raw_message:
        raw_detail = payload.get("detail") if payload else None
        raw_message = raw_detail if isinstance(raw_detail, str) else None
    # A canonical ApiProblem is already an intentionally public contract.
    # Non-canonical 5xx bodies can contain raw upstream exceptions or secrets.
    if status_code >= 500 and not is_canonical:
        message = default_problem_message(status_code)
    else:
        message = (raw_message or default_problem_message(status_code))[:1024]

    field_errors = _normalized_field_errors(payload.get("field_errors") if payload else None)
    problem = api_problem_payload(
        code=code,
        message=message,
        correlation_id=correlation_id,
        field_errors=field_errors,
    )
    return json.dumps(problem, ensure_ascii=False, separators=(",", ":")).encode()


class ApiProblemMiddleware:
    """Convert direct/middleware HTTP failures that bypass exception handlers."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        request_correlation_id(scope)
        path = str(scope.get("path") or "")
        if path in NON_API_PROBE_PATHS or path.startswith(NON_API_HTML_PREFIXES):
            await self.app(scope, receive, send)
            return
        error_start: Message | None = None
        error_body: list[bytes] = []

        async def normalize_send(message: Message) -> None:
            nonlocal error_start
            if message["type"] == "http.response.start":
                if int(message["status"]) < 400:
                    await send(message)
                    return
                error_start = message
                return

            if message["type"] != "http.response.body" or error_start is None:
                await send(message)
                return

            error_body.append(message.get("body", b""))
            if message.get("more_body", False):
                return

            correlation_id = request_correlation_id(scope)
            normalized = _problem_from_response(
                status_code=int(error_start["status"]),
                body=b"".join(error_body),
                correlation_id=correlation_id,
            )
            headers = [
                (key, value)
                for key, value in error_start.get("headers", [])
                if key.lower() not in {b"content-length", b"content-type", _REQUEST_ID_HEADER}
            ]
            headers.extend(
                [
                    (b"content-length", str(len(normalized)).encode("ascii")),
                    (b"content-type", b"application/json"),
                    (_REQUEST_ID_HEADER, correlation_id.encode("ascii")),
                ]
            )
            await send(
                {
                    **error_start,
                    "headers": headers,
                }
            )
            await send(
                {
                    "type": "http.response.body",
                    "body": b"" if scope.get("method") == "HEAD" else normalized,
                    "more_body": False,
                }
            )

        await self.app(scope, receive, normalize_send)


__all__ = [
    "ApiProblemMiddleware",
    "NON_API_HTML_PREFIXES",
    "NON_API_PROBE_PATHS",
    "api_problem_payload",
    "default_problem_code",
    "default_problem_message",
    "request_correlation_id",
]
