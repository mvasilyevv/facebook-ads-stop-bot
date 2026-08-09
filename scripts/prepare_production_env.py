#!/usr/bin/env python3
"""Create and validate a production dotenv file without printing secrets."""

from __future__ import annotations

import argparse
import base64
import os
import re
import tempfile
from pathlib import Path

KEY_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
BROWSER_AUTHORITY_CONSUME_URL = "https://app.adpulse.su/api/v1/internal/browser-operations/consume"
BROWSER_MAINTENANCE_CONSUME_URL = (
    "https://app.adpulse.su/api/v1/internal/browser-maintenance/consume"
)
PRIVATE_BROWSER_KEYS = frozenset(
    {
        "BROWSER_MAINTENANCE_CAPABILITY_SECRET",
        "BROWSER_OPERATION_CAPABILITY_SECRET",
        "BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE",
        "BROWSER_OPERATION_CAPABILITY_SECRET_META_API",
        "BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR",
        "BROWSER_AUTHORITY_CONSUMER_TOKEN",
    }
)
REMOVED_KEYS = (
    frozenset(
        {
            "DESKTOP_ACCESS_BASE_URL",
            "DESKTOP_RECOVERY_TTL_SECONDS",
            "DESKTOP_RECOVERY_KEY",
            "X_PANEL_RECOVERY_KEY",
            "DESKTOP_KASM_PUBLIC_ORIGIN",
            "DESKTOP_ACTIVE_TRANSPORT",
            "DESKTOP_KASM_ENABLED",
            "DESKTOP_KASMVNC_IMAGE",
            "VISION_X_TOKEN",
            "VISION_PROFILE_ID",
        }
    )
    | PRIVATE_BROWSER_KEYS
)
DURABLE_GENERATED_SECRETS = {
    "TMA_SESSION_SECRET": 32,
    "ADSETPRO_POSTBACK_SECRET": 32,
    "DESKTOP_KASM_SERVICE_PASSWORD": 32,
}


def parse_lines(text: str) -> tuple[list[str], dict[str, str]]:
    lines = text.splitlines()
    values: dict[str, str] = {}
    for line in lines:
        match = KEY_RE.match(line.strip())
        if not match:
            continue
        raw = match.group(2).strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in {'"', "'"}:
            raw = raw[1:-1]
        values[match.group(1)] = raw
    return lines, values


def render(lines: list[str], overrides: dict[str, str]) -> str:
    remaining = {key: value for key, value in overrides.items() if key not in REMOVED_KEYS}
    rendered: list[str] = []
    seen: set[str] = set()
    for line in lines:
        match = KEY_RE.match(line.strip())
        if not match:
            rendered.append(line)
            continue
        key = match.group(1)
        if key in REMOVED_KEYS:
            continue
        if key in seen:
            continue
        seen.add(key)
        if key in remaining:
            rendered.append(f"{key}={remaining.pop(key)}")
        else:
            rendered.append(line)
    if remaining:
        if rendered and rendered[-1]:
            rendered.append("")
        rendered.append("# Production deployment overrides")
        rendered.extend(f"{key}={value}" for key, value in remaining.items())
    return "\n".join(rendered).rstrip() + "\n"


def validate(values: dict[str, str]) -> list[str]:
    required = (
        "POSTGRES_PASSWORD",
        "FB_AGENT_BOOTSTRAP_CLUSTER_ID",
        "ENCRYPTION_KEY",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_WEBHOOK_SECRET",
        "ALERTMANAGER_WEBHOOK_SECRET",
        "TELEGRAM_OIDC_CLIENT_ID",
        "TELEGRAM_OIDC_CLIENT_SECRET",
        "TELEGRAM_OIDC_REDIRECT_URI",
        "API_KEY",
        "TMA_SESSION_SECRET",
        "ADSETPRO_POSTBACK_SECRET",
        "DESKTOP_WEBTOP_IMAGE",
        "DESKTOP_OWNER_TELEGRAM_USER_ID",
        "DESKTOP_PUBLIC_ORIGIN",
        "DESKTOP_KASM_SERVICE_USER",
        "DESKTOP_KASM_SERVICE_PASSWORD",
        "BROWSER_AUTHORITY_CONSUME_URL",
        "BROWSER_MAINTENANCE_CONSUME_URL",
    )
    errors = [f"{key} is empty" for key in required if not values.get(key, "").strip()]

    password = values.get("POSTGRES_PASSWORD", "")
    if password in {"fb_stop_bot", values.get("POSTGRES_DB", "fb_stop_bot")}:
        errors.append("POSTGRES_PASSWORD uses an insecure default")
    if password and len(password) < 16:
        errors.append("POSTGRES_PASSWORD must be at least 16 characters")
    if not re.fullmatch(
        r"[0-9a-f]{32}",
        values.get("FB_AGENT_BOOTSTRAP_CLUSTER_ID", ""),
    ):
        errors.append("FB_AGENT_BOOTSTRAP_CLUSTER_ID must be a 32-character hex id")
    if values.get("API_KEY") and len(values["API_KEY"]) < 24:
        errors.append("API_KEY must be at least 24 characters")
    if values.get("TMA_SESSION_SECRET") and len(values["TMA_SESSION_SECRET"]) < 32:
        errors.append("TMA_SESSION_SECRET must be at least 32 characters")
    if values.get("ADSETPRO_POSTBACK_SECRET") and len(values["ADSETPRO_POSTBACK_SECRET"]) < 32:
        errors.append("ADSETPRO_POSTBACK_SECRET must be at least 32 characters")
    for key in ("TELEGRAM_WEBHOOK_SECRET", "ALERTMANAGER_WEBHOOK_SECRET"):
        if values.get(key) and len(values[key]) < 32:
            errors.append(f"{key} must be at least 32 characters")
    if values.get("TELEGRAM_OIDC_CLIENT_ID") and not values["TELEGRAM_OIDC_CLIENT_ID"].isdigit():
        errors.append("TELEGRAM_OIDC_CLIENT_ID must be numeric")
    if (
        values.get("TELEGRAM_OIDC_CLIENT_SECRET")
        and len(values["TELEGRAM_OIDC_CLIENT_SECRET"]) < 32
    ):
        errors.append("TELEGRAM_OIDC_CLIENT_SECRET must be at least 32 characters")
    if values.get("TELEGRAM_OIDC_REDIRECT_URI") != (
        "https://app.adpulse.su/auth/telegram/callback"
    ):
        errors.append(
            "TELEGRAM_OIDC_REDIRECT_URI must be https://app.adpulse.su/auth/telegram/callback"
        )

    desktop_webtop_image = values.get("DESKTOP_WEBTOP_IMAGE", "")
    if desktop_webtop_image and not re.fullmatch(
        r"[^\s@]+@sha256:[0-9a-f]{64}", desktop_webtop_image
    ):
        errors.append("DESKTOP_WEBTOP_IMAGE must be an immutable image@sha256 reference")

    kasm_user = values.get("DESKTOP_KASM_SERVICE_USER", "")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", kasm_user):
        errors.append("DESKTOP_KASM_SERVICE_USER contains unsupported characters")
    if len(values.get("DESKTOP_KASM_SERVICE_PASSWORD", "")) < 32:
        errors.append("DESKTOP_KASM_SERVICE_PASSWORD must be at least 32 characters")

    if values.get("DESKTOP_PUBLIC_ORIGIN") != "https://desktop.adpulse.su":
        errors.append("DESKTOP_PUBLIC_ORIGIN must be https://desktop.adpulse.su")
    if values.get("BROWSER_AUTHORITY_CONSUME_URL") != BROWSER_AUTHORITY_CONSUME_URL:
        errors.append(f"BROWSER_AUTHORITY_CONSUME_URL must be {BROWSER_AUTHORITY_CONSUME_URL}")
    if values.get("BROWSER_MAINTENANCE_CONSUME_URL") != BROWSER_MAINTENANCE_CONSUME_URL:
        errors.append(f"BROWSER_MAINTENANCE_CONSUME_URL must be {BROWSER_MAINTENANCE_CONSUME_URL}")
    try:
        desktop_owner_id = int(values.get("DESKTOP_OWNER_TELEGRAM_USER_ID", "0"))
    except ValueError:
        desktop_owner_id = 0
    if desktop_owner_id <= 0:
        errors.append("DESKTOP_OWNER_TELEGRAM_USER_ID must be a positive integer")

    encryption_key = values.get("ENCRYPTION_KEY", "")
    if not encryption_key:
        errors.append("ENCRYPTION_KEY is required")
    else:
        try:
            decoded = base64.urlsafe_b64decode(encryption_key.encode())
            if len(decoded) != 32:
                raise ValueError
        except (ValueError, TypeError):
            errors.append("ENCRYPTION_KEY must be a valid 32-byte Fernet key")
    if not values.get("ENCRYPTION_KEY_VERIFY", ""):
        errors.append("ENCRYPTION_KEY_VERIFY is required")

    expected = {
        "REQUIRE_API_KEY": "true",
        "TRUST_PROXY_HEADERS": "true",
        "DEV_TOOLS_ENABLED": "false",
        "LOG_FORMAT": "json",
        "DEPLOYMENT_ENVIRONMENT": "production",
    }
    for key, expected_value in expected.items():
        if values.get(key, "").lower() != expected_value:
            errors.append(f"{key} must be {expected_value}")
    if values.get("FRONTEND_ORIGIN") != "https://app.adpulse.su":
        errors.append("FRONTEND_ORIGIN must be https://app.adpulse.su")
    if values.get("WEB_APP_URL") != "https://app.adpulse.su/tma/":
        errors.append("WEB_APP_URL must be https://app.adpulse.su/tma/")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--public-url", default="https://app.adpulse.su")
    parser.add_argument("--desktop-webtop-image")
    parser.add_argument("--bootstrap-secrets", type=Path)
    parser.add_argument("--validate-only", action="store_true")
    args = parser.parse_args()

    if not args.input.is_file():
        parser.error(f"input file does not exist: {args.input}")
    lines, current = parse_lines(args.input.read_text(encoding="utf-8"))
    bootstrap = current
    if args.bootstrap_secrets is not None:
        if not args.bootstrap_secrets.is_file() or args.bootstrap_secrets.is_symlink():
            parser.error(f"bootstrap secrets file is unavailable: {args.bootstrap_secrets}")
        _, bootstrap = parse_lines(args.bootstrap_secrets.read_text(encoding="utf-8"))
    public_url = args.public_url.rstrip("/")
    if public_url != "https://app.adpulse.su":
        parser.error("only the canonical public URL https://app.adpulse.su is supported")
    bootstrap_cluster_id = bootstrap.get("FB_AGENT_BOOTSTRAP_CLUSTER_ID", "")
    bootstrap_postgres_password = bootstrap.get("POSTGRES_PASSWORD", "")
    if not re.fullmatch(r"[0-9a-f]{32}", bootstrap_cluster_id):
        parser.error("bootstrap secrets contain an invalid cluster id")
    if len(bootstrap_postgres_password) < 16:
        parser.error("bootstrap secrets contain an invalid PostgreSQL password")
    current_postgres_password = current.get("POSTGRES_PASSWORD", "")
    if (
        current_postgres_password
        and len(current_postgres_password) >= 16
        and current_postgres_password != bootstrap_postgres_password
    ):
        parser.error("shared POSTGRES_PASSWORD conflicts with durable bootstrap state")
    durable_generated: dict[str, str] = {}
    for key, minimum_length in DURABLE_GENERATED_SECRETS.items():
        durable_value = bootstrap.get(key, "")
        current_value = current.get(key, "")
        if len(durable_value) < minimum_length:
            parser.error(f"bootstrap secrets contain an invalid {key}")
        if current_value and current_value != durable_value:
            parser.error(f"shared {key} conflicts with durable secret state")
        durable_generated[key] = durable_value
    overrides = {
        "FB_AGENT_BOOTSTRAP_CLUSTER_ID": bootstrap_cluster_id,
        "POSTGRES_PASSWORD": bootstrap_postgres_password,
        "FRONTEND_ORIGIN": public_url,
        "WEB_APP_URL": f"{public_url}/tma/",
        "DESKTOP_PUBLIC_ORIGIN": "https://desktop.adpulse.su",
        "BROWSER_AUTHORITY_CONSUME_URL": BROWSER_AUTHORITY_CONSUME_URL,
        "BROWSER_MAINTENANCE_CONSUME_URL": BROWSER_MAINTENANCE_CONSUME_URL,
        "DESKTOP_KASM_SERVICE_USER": current.get("DESKTOP_KASM_SERVICE_USER") or "adpulse-desktop",
        "DESKTOP_KASM_SERVICE_PASSWORD": durable_generated["DESKTOP_KASM_SERVICE_PASSWORD"],
        "REQUIRE_API_KEY": "true",
        "TRUST_PROXY_HEADERS": "true",
        "DEV_TOOLS_ENABLED": "false",
        "LOG_FORMAT": "json",
        "DEPLOYMENT_ENVIRONMENT": "production",
        "TMA_SESSION_SECRET": durable_generated["TMA_SESSION_SECRET"],
        # AdSet.pro умеет только GET pixel без custom header, поэтому этот секрет
        # используется query-token'ом. Генерируем один раз и сохраняем между release.
        "ADSETPRO_POSTBACK_SECRET": durable_generated["ADSETPRO_POSTBACK_SECRET"],
        "DESKTOP_WEBTOP_IMAGE": args.desktop_webtop_image
        or current.get("DESKTOP_WEBTOP_IMAGE", ""),
    }
    rendered = render(lines, overrides)
    _, final_values = parse_lines(rendered)
    errors = validate(final_values)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=os.sys.stderr)
        return 2

    if args.validate_only:
        print("Production environment validation: OK")
        return 0
    if args.output is None:
        parser.error("--output is required unless --validate-only is used")

    args.output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{args.output.name}.", dir=args.output.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(rendered)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, args.output)
        directory_descriptor = os.open(args.output.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"Production environment prepared: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
