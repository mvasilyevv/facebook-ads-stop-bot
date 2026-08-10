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
        "FB_AGENT_BOOTSTRAP_CLUSTER_ID": "c" * 32,
        "ENCRYPTION_KEY": base64.urlsafe_b64encode(b"k" * 32).decode(),
        "ENCRYPTION_KEY_VERIFY": "verified-ciphertext",
        "TELEGRAM_BOT_TOKEN": "telegram-token",
        "TELEGRAM_WEBHOOK_SECRET": "w" * 48,
        "ALERTMANAGER_WEBHOOK_SECRET": "m" * 48,
        "TELEGRAM_OIDC_CLIENT_ID": "123456789",
        "TELEGRAM_OIDC_CLIENT_SECRET": "o" * 48,
        "TELEGRAM_OIDC_REDIRECT_URI": "https://app.adpulse.su/auth/telegram/callback",
        "API_KEY": "a" * 32,
        "TMA_SESSION_SECRET": "t" * 48,
        "ADSETPRO_POSTBACK_SECRET": "p" * 48,
        "DESKTOP_WEBTOP_IMAGE": "registry.example/webtop@sha256:" + "a" * 64,
        "DESKTOP_OWNER_TELEGRAM_USER_ID": "911436108",
        "DESKTOP_PUBLIC_ORIGIN": "https://desktop.adpulse.su",
        "BROWSER_AUTHORITY_CONSUME_URL": ENV.BROWSER_AUTHORITY_CONSUME_URL,
        "BROWSER_MAINTENANCE_CONSUME_URL": (ENV.BROWSER_MAINTENANCE_CONSUME_URL),
        "DESKTOP_KASM_SERVICE_USER": "adpulse-desktop",
        "DESKTOP_KASM_SERVICE_PASSWORD": "k" * 48,
        "REQUIRE_API_KEY": "true",
        "TRUST_PROXY_HEADERS": "true",
        "DEV_TOOLS_ENABLED": "false",
        "LOG_FORMAT": "json",
        "DEPLOYMENT_ENVIRONMENT": "production",
        "FRONTEND_ORIGIN": "https://app.adpulse.su",
        "WEB_APP_URL": "https://app.adpulse.su/tma/",
    }


def test_render_replaces_once_and_preserves_unrelated_lines() -> None:
    lines = ["# keep", "REQUIRE_API_KEY=false", "REQUIRE_API_KEY=false", "CUSTOM=value"]
    rendered = ENV.render(lines, {"REQUIRE_API_KEY": "true", "NEW_KEY": "new"})

    assert rendered.count("REQUIRE_API_KEY=") == 1
    assert "REQUIRE_API_KEY=true" in rendered
    assert "CUSTOM=value" in rendered
    assert "NEW_KEY=new" in rendered


def test_render_removes_retired_runtime_keys() -> None:
    rendered = ENV.render(
        [
            "DESKTOP_ACCESS_BASE_URL=https://desktop.adpulse.su",
            "DESKTOP_RECOVERY_KEY=retired",
            "X_PANEL_RECOVERY_KEY=retired",
            "DESKTOP_ACTIVE_TRANSPORT=retired",
            "DESKTOP_KASM_ENABLED=true",
            "DESKTOP_KASMVNC_IMAGE=retired",
            "VISION_X_TOKEN=must-not-survive",
            "VISION_PROFILE_ID=must-not-survive",
            "BROWSER_MAINTENANCE_CAPABILITY_SECRET=must-not-survive",
            "BROWSER_OPERATION_CAPABILITY_SECRET=must-not-survive",
            "BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE=must-not-survive",
            "BROWSER_OPERATION_CAPABILITY_SECRET_META_API=must-not-survive",
            "BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR=must-not-survive",
            "BROWSER_AUTHORITY_CONSUMER_TOKEN=must-not-survive",
            "CUSTOM=value",
        ],
        {
            "VISION_X_TOKEN": "override-must-not-survive",
            "VISION_PROFILE_ID": "override-must-not-survive",
            "BROWSER_MAINTENANCE_CAPABILITY_SECRET": "override-must-not-survive",
            "BROWSER_OPERATION_CAPABILITY_SECRET": "override-must-not-survive",
            "BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE": ("override-must-not-survive"),
            "BROWSER_OPERATION_CAPABILITY_SECRET_META_API": ("override-must-not-survive"),
            "BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR": ("override-must-not-survive"),
            "BROWSER_AUTHORITY_CONSUMER_TOKEN": "override-must-not-survive",
        },
    )

    assert "DESKTOP_ACCESS_BASE_URL" not in rendered
    assert "DESKTOP_RECOVERY_KEY" not in rendered
    assert "X_PANEL_RECOVERY_KEY" not in rendered
    assert "DESKTOP_ACTIVE_TRANSPORT" not in rendered
    assert "DESKTOP_KASM_ENABLED" not in rendered
    assert "DESKTOP_KASMVNC_IMAGE" not in rendered
    assert "VISION_X_TOKEN" not in rendered
    assert "VISION_PROFILE_ID" not in rendered
    for key in ENV.PRIVATE_BROWSER_KEYS:
        assert key not in rendered
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


def test_validate_requires_complete_encryption_pair() -> None:
    values = _valid_values()
    values["ENCRYPTION_KEY"] = ""
    values["ENCRYPTION_KEY_VERIFY"] = ""

    errors = ENV.validate(values)

    assert "ENCRYPTION_KEY is required" in errors
    assert "ENCRYPTION_KEY_VERIFY is required" in errors


def test_validate_rejects_short_postback_secret() -> None:
    values = _valid_values()
    values["ADSETPRO_POSTBACK_SECRET"] = "short"

    assert "ADSETPRO_POSTBACK_SECRET must be at least 32 characters" in ENV.validate(values)


def test_validate_rejects_missing_or_short_webhook_secrets() -> None:
    values = _valid_values()
    values["TELEGRAM_WEBHOOK_SECRET"] = ""
    values["ALERTMANAGER_WEBHOOK_SECRET"] = "short"

    errors = ENV.validate(values)

    assert "TELEGRAM_WEBHOOK_SECRET is empty" in errors
    assert "ALERTMANAGER_WEBHOOK_SECRET must be at least 32 characters" in errors


def test_validate_rejects_missing_or_misdirected_telegram_oidc() -> None:
    values = _valid_values()
    values["TELEGRAM_OIDC_CLIENT_SECRET"] = ""
    values["TELEGRAM_OIDC_REDIRECT_URI"] = "https://evil.example/callback"

    errors = ENV.validate(values)

    assert "TELEGRAM_OIDC_CLIENT_SECRET is empty" in errors
    assert (
        "TELEGRAM_OIDC_REDIRECT_URI must be https://app.adpulse.su/auth/telegram/callback" in errors
    )


def test_validate_rejects_invalid_desktop_secrets() -> None:
    values = _valid_values()
    values["DESKTOP_WEBTOP_IMAGE"] = "registry.example/webtop:latest"
    values["DESKTOP_KASM_SERVICE_PASSWORD"] = "short"

    errors = ENV.validate(values)

    assert "DESKTOP_WEBTOP_IMAGE must be an immutable image@sha256 reference" in errors
    assert "DESKTOP_KASM_SERVICE_PASSWORD must be at least 32 characters" in errors


def test_validate_requires_canonical_https_browser_authority_url() -> None:
    values = _valid_values()
    values["BROWSER_AUTHORITY_CONSUME_URL"] = (
        "https://app.adpulse.su/api/v1/internal/browser-operations/consume?token=must-not-be-in-url"
    )

    errors = ENV.validate(values)

    assert errors == ["BROWSER_AUTHORITY_CONSUME_URL must be " + ENV.BROWSER_AUTHORITY_CONSUME_URL]
    assert ENV.BROWSER_AUTHORITY_CONSUME_URL.startswith("https://")
    assert "?" not in ENV.BROWSER_AUTHORITY_CONSUME_URL

    values = _valid_values()
    values["BROWSER_MAINTENANCE_CONSUME_URL"] = (
        ENV.BROWSER_MAINTENANCE_CONSUME_URL + "?token=must-not-be-in-url"
    )
    assert ENV.validate(values) == [
        "BROWSER_MAINTENANCE_CONSUME_URL must be " + ENV.BROWSER_MAINTENANCE_CONSUME_URL
    ]
    assert ENV.BROWSER_MAINTENANCE_CONSUME_URL.startswith("https://")
    assert "?" not in ENV.BROWSER_MAINTENANCE_CONSUME_URL


def test_parse_lines_does_not_include_comments() -> None:
    _, values = ENV.parse_lines("# SECRET=nope\nexport REAL='value'\n")
    assert values == {"REAL": "value"}


def test_render_accepts_ci_supplied_immutable_webtop_artifact() -> None:
    image = "ghcr.io/example/vision-webtop@sha256:" + "b" * 64
    rendered = ENV.render([], {"DESKTOP_WEBTOP_IMAGE": image})

    _, values = ENV.parse_lines(rendered)
    assert values["DESKTOP_WEBTOP_IMAGE"] == image
    assert not [error for error in ENV.validate(_valid_values() | values) if "WEBTOP" in error]
