from __future__ import annotations

import base64
import importlib.util
from pathlib import Path


def _load_module():
    path = Path(__file__).parents[2] / "scripts" / "prepare_production_env.py"
    spec = importlib.util.spec_from_file_location("prepare_production_env", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


ENV = _load_module()


def _valid_values() -> dict[str, str]:
    return {
        "POSTGRES_DB": "fb_stop_bot",
        "POSTGRES_PASSWORD": "p" * 32,
        "ENCRYPTION_KEY": base64.urlsafe_b64encode(b"k" * 32).decode(),
        "VISION_X_TOKEN": "vision-token",
        "VISION_PROFILE_ID": "profile-id",
        "TELEGRAM_BOT_TOKEN": "telegram-token",
        "TELEGRAM_OIDC_CLIENT_ID": "123456789",
        "TELEGRAM_OIDC_CLIENT_SECRET": "o" * 48,
        "TELEGRAM_OIDC_REDIRECT_URI": "https://app.adpulse.su/auth/telegram/callback",
        "API_KEY": "a" * 32,
        "TMA_SESSION_SECRET": "t" * 48,
        "ADSETPRO_POSTBACK_SECRET": "p" * 48,
        "DESKTOP_WEBTOP_IMAGE": "registry.example/webtop@sha256:" + "a" * 64,
        "DESKTOP_KASMVNC_IMAGE": "registry.example/kasm@sha256:" + "b" * 64,
        "DESKTOP_OWNER_TELEGRAM_USER_ID": "911436108",
        "DESKTOP_PUBLIC_ORIGIN": "https://desktop.adpulse.su",
        "DESKTOP_KASM_SERVICE_USER": "adpulse-desktop",
        "DESKTOP_KASM_SERVICE_PASSWORD": "k" * 48,
        "TRACKER_AUTO_CANCEL_ENABLED": "false",
        "REQUIRE_API_KEY": "true",
        "TRUST_PROXY_HEADERS": "true",
        "DEV_TOOLS_ENABLED": "false",
        "LOG_FORMAT": "json",
        "SENTRY_ENVIRONMENT": "production",
        "FRONTEND_ORIGIN": "https://app.example.org",
        "WEB_APP_URL": "https://app.example.org/tma/",
    }


def test_render_replaces_once_and_preserves_unrelated_lines() -> None:
    lines = ["# keep", "REQUIRE_API_KEY=false", "REQUIRE_API_KEY=false", "CUSTOM=value"]
    rendered = ENV.render(lines, {"REQUIRE_API_KEY": "true", "NEW_KEY": "new"})

    assert rendered.count("REQUIRE_API_KEY=") == 1
    assert "REQUIRE_API_KEY=true" in rendered
    assert "CUSTOM=value" in rendered
    assert "NEW_KEY=new" in rendered


def test_render_removes_retired_desktop_flags() -> None:
    rendered = ENV.render(
        [
            "DESKTOP_ACCESS_BASE_URL=https://desktop.adpulse.su",
            "DESKTOP_RECOVERY_KEY=retired",
            "X_PANEL_RECOVERY_KEY=retired",
            "DESKTOP_ACTIVE_TRANSPORT=retired",
            "DESKTOP_KASM_ENABLED=true",
            "CUSTOM=value",
        ],
        {},
    )

    assert "DESKTOP_ACCESS_BASE_URL" not in rendered
    assert "DESKTOP_RECOVERY_KEY" not in rendered
    assert "X_PANEL_RECOVERY_KEY" not in rendered
    assert "DESKTOP_ACTIVE_TRANSPORT" not in rendered
    assert "DESKTOP_KASM_ENABLED" not in rendered
    assert "CUSTOM=value" in rendered


def test_validate_accepts_complete_production_environment() -> None:
    assert ENV.validate(_valid_values()) == []


def test_validate_rejects_dev_password_and_flags() -> None:
    values = _valid_values()
    values["POSTGRES_PASSWORD"] = "fb_stop_bot"
    values["REQUIRE_API_KEY"] = "false"

    errors = ENV.validate(values)

    assert "POSTGRES_PASSWORD uses an insecure default" in errors
    assert "REQUIRE_API_KEY must be true" in errors


def test_validate_rejects_short_postback_secret() -> None:
    values = _valid_values()
    values["ADSETPRO_POSTBACK_SECRET"] = "short"

    assert "ADSETPRO_POSTBACK_SECRET must be at least 32 characters" in ENV.validate(values)


def test_validate_rejects_missing_or_misdirected_telegram_oidc() -> None:
    values = _valid_values()
    values["TELEGRAM_OIDC_CLIENT_SECRET"] = ""
    values["TELEGRAM_OIDC_REDIRECT_URI"] = "https://evil.example/callback"

    errors = ENV.validate(values)

    assert "TELEGRAM_OIDC_CLIENT_SECRET is empty" in errors
    assert (
        "TELEGRAM_OIDC_REDIRECT_URI must be https://app.adpulse.su/auth/telegram/callback" in errors
    )


def test_validate_accepts_explicit_auto_cancel_rollout_boolean_only() -> None:
    values = _valid_values()
    values["TRACKER_AUTO_CANCEL_ENABLED"] = "true"
    assert ENV.validate(values) == []

    values["TRACKER_AUTO_CANCEL_ENABLED"] = "maybe"
    assert "TRACKER_AUTO_CANCEL_ENABLED must be true or false" in ENV.validate(values)


def test_validate_rejects_invalid_desktop_secrets() -> None:
    values = _valid_values()
    values["DESKTOP_WEBTOP_IMAGE"] = "registry.example/webtop:latest"
    values["DESKTOP_KASMVNC_IMAGE"] = "registry.example/kasm:latest"
    values["DESKTOP_KASM_SERVICE_PASSWORD"] = "short"

    errors = ENV.validate(values)

    assert "DESKTOP_WEBTOP_IMAGE must be an immutable image@sha256 reference" in errors
    assert "DESKTOP_KASMVNC_IMAGE must be an immutable image@sha256 reference" in errors
    assert "DESKTOP_KASM_SERVICE_PASSWORD must be at least 32 characters" in errors


def test_parse_lines_does_not_include_comments() -> None:
    _, values = ENV.parse_lines("# SECRET=nope\nexport REAL='value'\n")
    assert values == {"REAL": "value"}


def test_render_accepts_ci_supplied_immutable_webtop_artifact() -> None:
    image = "ghcr.io/example/vision-webtop@sha256:" + "b" * 64
    rendered = ENV.render([], {"DESKTOP_WEBTOP_IMAGE": image})

    _, values = ENV.parse_lines(rendered)
    assert values["DESKTOP_WEBTOP_IMAGE"] == image
    assert not [error for error in ENV.validate(_valid_values() | values) if "WEBTOP" in error]
