from __future__ import annotations

import pytest
from fastapi import HTTPException, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.testclient import TestClient

from apps.api.main import create_app
from core.adset_pro import (
    AdsetProError,
)
from core.adset_pro import (
    AuthError as AdsetProAuthError,
)
from core.adset_pro import (
    NotFoundError as AdsetProNotFoundError,
)
from core.adset_pro import (
    RateLimitedError as AdsetProRateLimitedError,
)
from core.adset_pro import (
    TemporaryError as AdsetProTemporaryError,
)
from core.meta_api.errors import (
    MetaApiError,
)
from core.meta_api.errors import (
    NotFoundError as MetaNotFoundError,
)
from core.meta_api.errors import (
    PermissionError as MetaPermissionError,
)
from core.meta_api.errors import (
    RateLimitedError as MetaRateLimitedError,
)
from core.meta_api.errors import (
    SessionUnavailableError as MetaSessionUnavailableError,
)
from core.meta_api.errors import (
    TokenInvalidError as MetaTokenInvalidError,
)

_FIELDS = {"code", "message", "correlation_id", "field_errors"}


def _assert_problem(response, *, status: int, code: str) -> dict:
    assert response.status_code == status
    payload = response.json()
    assert set(payload) == _FIELDS
    assert payload["code"] == code
    assert payload["correlation_id"]
    assert response.headers["x-request-id"] == payload["correlation_id"]
    return payload


def test_http_exception_preserves_status_headers_and_public_4xx_message() -> None:
    app = create_app()

    @app.get("/__test__/conflict")
    async def conflict() -> None:
        raise HTTPException(
            status_code=409,
            detail="Состояние изменилось",
            headers={"Retry-After": "7"},
        )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/__test__/conflict",
            headers={"X-Request-Id": "request.trace-123"},
        )

    payload = _assert_problem(response, status=409, code="conflict")
    assert payload["message"] == "Состояние изменилось"
    assert payload["correlation_id"] == "request.trace-123"
    assert payload["field_errors"] is None
    assert response.headers["retry-after"] == "7"


def test_not_found_uses_the_same_contract_and_generated_correlation_id() -> None:
    app = create_app()

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/__test__/missing")

    payload = _assert_problem(response, status=404, code="not_found")
    assert payload["message"] == "Not Found"
    assert len(payload["correlation_id"]) == 32


def test_validation_error_has_field_errors_without_echoing_input() -> None:
    app = create_app()

    @app.get("/__test__/validation")
    async def validation(value: int = Query(gt=0)) -> dict[str, int]:
        return {"value": value}

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get(
            "/__test__/validation",
            params={"value": "private-invalid-value"},
        )

    payload = _assert_problem(response, status=422, code="validation_error")
    assert payload["message"] == "Параметры запроса не прошли проверку"
    assert "query.value" in payload["field_errors"]
    assert "private-invalid-value" not in response.text


@pytest.mark.parametrize(
    ("error", "status", "code"),
    [
        (AdsetProAuthError("api_key=adset-secret"), 401, "adsetpro_auth"),
        (AdsetProNotFoundError("raw upstream body"), 404, "adsetpro_not_found"),
        (AdsetProRateLimitedError("raw upstream body"), 429, "adsetpro_rate_limited"),
        (AdsetProTemporaryError("Bearer adset-secret"), 503, "adsetpro_temporary"),
        (AdsetProError("Bearer adset-secret"), 502, "adsetpro"),
        (MetaTokenInvalidError("access_token=meta-secret"), 401, "meta_token_invalid"),
        (MetaPermissionError("raw Graph response"), 403, "meta_permission"),
        (MetaNotFoundError("raw Graph response"), 404, "meta_not_found"),
        (MetaRateLimitedError("raw Graph response"), 429, "meta_rate_limited"),
        (
            MetaSessionUnavailableError("cookie=session-secret"),
            503,
            "meta_session_unavailable",
        ),
        (MetaApiError("access_token=meta-secret"), 502, "meta_api"),
    ],
)
def test_domain_errors_keep_status_and_never_expose_raw_exception(
    error: Exception,
    status: int,
    code: str,
) -> None:
    app = create_app()

    @app.get("/__test__/domain-error")
    async def domain_error() -> None:
        raise error

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/__test__/domain-error")

    _assert_problem(response, status=status, code=code)
    assert str(error) not in response.text


def test_unhandled_and_direct_5xx_responses_do_not_leak_secrets() -> None:
    app = create_app()

    @app.get("/__test__/unhandled")
    async def unhandled() -> None:
        raise RuntimeError("Bearer unhandled-secret")

    @app.get("/__test__/direct-error")
    async def direct_error() -> JSONResponse:
        return JSONResponse(
            status_code=503,
            content={"detail": "signed_url=https://user:password@example.test"},
        )

    with TestClient(app, raise_server_exceptions=False) as client:
        unhandled_response = client.get("/__test__/unhandled")
        direct_response = client.get("/__test__/direct-error")

    unhandled_payload = _assert_problem(
        unhandled_response,
        status=500,
        code="internal_error",
    )
    assert unhandled_payload["message"] == "Внутренняя ошибка сервиса"
    assert "unhandled-secret" not in unhandled_response.text

    direct_payload = _assert_problem(
        direct_response,
        status=503,
        code="service_unavailable",
    )
    assert direct_payload["message"] == "Сервис временно недоступен"
    assert "password" not in direct_response.text


def test_browser_auth_error_page_is_not_rewritten_as_api_problem() -> None:
    app = create_app()

    @app.get("/auth/__test__/denied", include_in_schema=False)
    async def denied() -> HTMLResponse:
        return HTMLResponse(
            "<!doctype html><title>Denied</title><p>Ticket already used</p>",
            status_code=403,
        )

    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.get("/auth/__test__/denied")

    assert response.status_code == 403
    assert response.headers["content-type"].startswith("text/html")
    assert "Ticket already used" in response.text
    assert "correlation_id" not in response.text


def test_openapi_declares_api_problem_for_validation_and_default_errors() -> None:
    app = create_app()

    @app.get("/__test__/openapi")
    async def openapi_route(value: int) -> dict[str, int]:
        return {"value": value}

    schema = app.openapi()
    operation = schema["paths"]["/__test__/openapi"]["get"]
    problem_ref = {"$ref": "#/components/schemas/ApiProblem"}

    assert operation["responses"]["422"]["content"]["application/json"]["schema"] == problem_ref
    assert operation["responses"]["default"]["content"]["application/json"]["schema"] == problem_ref
    assert set(schema["components"]["schemas"]["ApiProblem"]["required"]) == _FIELDS
    assert "HTTPValidationError" not in schema["components"]["schemas"]
    assert "ValidationError" not in schema["components"]["schemas"]
