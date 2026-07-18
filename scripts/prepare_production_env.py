#!/usr/bin/env python3
"""Create and validate a production dotenv file without printing secrets."""

from __future__ import annotations

import argparse
import base64
import os
import re
import secrets
import tempfile
from pathlib import Path

KEY_RE = re.compile(r"^(?:export\s+)?([A-Za-z_][A-Za-z0-9_]*)=(.*)$")
REMOVED_DESKTOP_KEYS = frozenset(
    {
        "DESKTOP_ACCESS_BASE_URL",
        "DESKTOP_RECOVERY_TTL_SECONDS",
        "DESKTOP_RECOVERY_KEY",
        "X_PANEL_RECOVERY_KEY",
        "DESKTOP_KASM_PUBLIC_ORIGIN",
        "DESKTOP_ACTIVE_TRANSPORT",
        "DESKTOP_KASM_ENABLED",
    }
)


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
    remaining = dict(overrides)
    rendered: list[str] = []
    seen: set[str] = set()
    for line in lines:
        match = KEY_RE.match(line.strip())
        if not match:
            rendered.append(line)
            continue
        key = match.group(1)
        if key in REMOVED_DESKTOP_KEYS:
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
        "ENCRYPTION_KEY",
        "VISION_X_TOKEN",
        "VISION_PROFILE_ID",
        "TELEGRAM_BOT_TOKEN",
        "TELEGRAM_OIDC_CLIENT_ID",
        "TELEGRAM_OIDC_CLIENT_SECRET",
        "TELEGRAM_OIDC_REDIRECT_URI",
        "API_KEY",
        "TMA_SESSION_SECRET",
        "ADSETPRO_POSTBACK_SECRET",
        "DESKTOP_WEBTOP_IMAGE",
        "DESKTOP_KASMVNC_IMAGE",
        "DESKTOP_OWNER_TELEGRAM_USER_ID",
        "DESKTOP_PUBLIC_ORIGIN",
        "DESKTOP_KASM_SERVICE_USER",
        "DESKTOP_KASM_SERVICE_PASSWORD",
    )
    errors = [f"{key} is empty" for key in required if not values.get(key, "").strip()]

    password = values.get("POSTGRES_PASSWORD", "")
    if password in {"fb_stop_bot", values.get("POSTGRES_DB", "fb_stop_bot")}:
        errors.append("POSTGRES_PASSWORD uses an insecure default")
    if password and len(password) < 16:
        errors.append("POSTGRES_PASSWORD must be at least 16 characters")
    if values.get("API_KEY") and len(values["API_KEY"]) < 24:
        errors.append("API_KEY must be at least 24 characters")
    if values.get("TMA_SESSION_SECRET") and len(values["TMA_SESSION_SECRET"]) < 32:
        errors.append("TMA_SESSION_SECRET must be at least 32 characters")
    if values.get("ADSETPRO_POSTBACK_SECRET") and len(values["ADSETPRO_POSTBACK_SECRET"]) < 32:
        errors.append("ADSETPRO_POSTBACK_SECRET must be at least 32 characters")
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

    kasm_image = values.get("DESKTOP_KASMVNC_IMAGE", "")
    if kasm_image and not re.fullmatch(r"[^\s@]+@sha256:[0-9a-f]{64}", kasm_image):
        errors.append("DESKTOP_KASMVNC_IMAGE must be an immutable image@sha256 reference")
    kasm_user = values.get("DESKTOP_KASM_SERVICE_USER", "")
    if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}", kasm_user):
        errors.append("DESKTOP_KASM_SERVICE_USER contains unsupported characters")
    if len(values.get("DESKTOP_KASM_SERVICE_PASSWORD", "")) < 32:
        errors.append("DESKTOP_KASM_SERVICE_PASSWORD must be at least 32 characters")

    if values.get("DESKTOP_PUBLIC_ORIGIN") != "https://desktop.adpulse.su":
        errors.append("DESKTOP_PUBLIC_ORIGIN must be https://desktop.adpulse.su")
    try:
        desktop_owner_id = int(values.get("DESKTOP_OWNER_TELEGRAM_USER_ID", "0"))
    except ValueError:
        desktop_owner_id = 0
    if desktop_owner_id <= 0:
        errors.append("DESKTOP_OWNER_TELEGRAM_USER_ID must be a positive integer")

    encryption_key = values.get("ENCRYPTION_KEY", "")
    if encryption_key:
        try:
            decoded = base64.urlsafe_b64decode(encryption_key.encode())
            if len(decoded) != 32:
                raise ValueError
        except (ValueError, TypeError):
            errors.append("ENCRYPTION_KEY must be a valid 32-byte Fernet key")

    expected = {
        "REQUIRE_API_KEY": "true",
        "TRUST_PROXY_HEADERS": "true",
        "DEV_TOOLS_ENABLED": "false",
        "LOG_FORMAT": "json",
        "SENTRY_ENVIRONMENT": "production",
    }
    for key, expected_value in expected.items():
        if values.get(key, "").lower() != expected_value:
            errors.append(f"{key} must be {expected_value}")
    if values.get("TRACKER_AUTO_CANCEL_ENABLED", "false").lower() not in {"true", "false"}:
        errors.append("TRACKER_AUTO_CANCEL_ENABLED must be true or false")
    for key in ("FRONTEND_ORIGIN", "WEB_APP_URL"):
        if not values.get(key, "").startswith("https://"):
            errors.append(f"{key} must use https://")
    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--public-url", default="https://app.adpulse.su")
    parser.add_argument("--desktop-webtop-image")
    parser.add_argument("--desktop-kasmvnc-image")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--generate-postgres-password-if-insecure", action="store_true")
    args = parser.parse_args()

    if not args.input.is_file():
        parser.error(f"input file does not exist: {args.input}")
    lines, current = parse_lines(args.input.read_text(encoding="utf-8"))
    public_url = args.public_url.rstrip("/")
    postgres_password = current.get("POSTGRES_PASSWORD", "")
    postgres_is_insecure = (
        not postgres_password
        or postgres_password == "fb_stop_bot"
        or postgres_password == current.get("POSTGRES_DB", "fb_stop_bot")
        or len(postgres_password) < 16
    )
    overrides = {
        "FRONTEND_ORIGIN": public_url,
        "WEB_APP_URL": f"{public_url}/tma/",
        "DESKTOP_PUBLIC_ORIGIN": "https://desktop.adpulse.su",
        "DESKTOP_KASM_SERVICE_USER": current.get("DESKTOP_KASM_SERVICE_USER") or "adpulse-desktop",
        "DESKTOP_KASM_SERVICE_PASSWORD": current.get("DESKTOP_KASM_SERVICE_PASSWORD")
        or secrets.token_urlsafe(48),
        "REQUIRE_API_KEY": "true",
        "TRUST_PROXY_HEADERS": "true",
        "DEV_TOOLS_ENABLED": "false",
        "LOG_FORMAT": "json",
        "SENTRY_ENVIRONMENT": "production",
        "TMA_SESSION_SECRET": current.get("TMA_SESSION_SECRET") or secrets.token_urlsafe(48),
        # AdSet.pro умеет только GET pixel без custom header, поэтому этот секрет
        # используется query-token'ом. Генерируем один раз и сохраняем между release.
        "ADSETPRO_POSTBACK_SECRET": current.get("ADSETPRO_POSTBACK_SECRET")
        or secrets.token_urlsafe(48),
        "DESKTOP_WEBTOP_IMAGE": args.desktop_webtop_image
        or current.get("DESKTOP_WEBTOP_IMAGE", ""),
        "DESKTOP_KASMVNC_IMAGE": args.desktop_kasmvnc_image
        or current.get("DESKTOP_KASMVNC_IMAGE", ""),
        # Preserve an explicitly enabled staged rollout; new environments start
        # in shadow mode until a full-day reconciliation is accepted.
        "TRACKER_AUTO_CANCEL_ENABLED": current.get("TRACKER_AUTO_CANCEL_ENABLED") or "false",
    }
    if postgres_is_insecure and args.generate_postgres_password_if_insecure:
        overrides["POSTGRES_PASSWORD"] = secrets.token_urlsafe(48)
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
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"Production environment prepared: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
