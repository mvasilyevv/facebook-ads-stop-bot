"""Canonical source configuration and sealed single-slot Compose runtime."""

from __future__ import annotations

import base64
import os
import re
import secrets
import stat
import uuid
from dataclasses import dataclass
from pathlib import Path

from fbctl.bundle import IMAGE_KEYS, RELEASE_SCHEMA, verify_materialized_resources
from fbctl.errors import FbctlError
from fbctl.files import (
    IMAGE_DIGEST,
    atomic_write,
    parse_dotenv,
    parse_dotenv_payload,
    require_absolute_path,
    require_directory,
    require_private_file,
    require_release_id,
)

RUNTIME_SCHEMA = "fb-agent-production-runtime/v2"
PUBLIC_URL = "https://app.adpulse.su"
INFRA_PROJECT_NAME = "fb_agent_infra"
INFRA_HOST_PORTS = (
    ("POSTGRES_HOST_PORT", "5433"),
    ("REDIS_HOST_PORT", "6380"),
)
POSTGRES_IMAGE = (
    "postgres:16@sha256:33f923b05f64ca54ac4401c01126a6b92afe839a0aa0a52bc5aeb5cc958e5f20"
)
GENERATED_SECRETS = {
    "TELEGRAM_WEBHOOK_SECRET": 32,
    "ALERTMANAGER_WEBHOOK_SECRET": 32,
    "TMA_SESSION_SECRET": 32,
    "ADSETPRO_POSTBACK_SECRET": 32,
    "DESKTOP_KASM_SERVICE_PASSWORD": 32,
    "BROWSER_MAINTENANCE_CAPABILITY_SECRET": 48,
    "BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE": 48,
    "BROWSER_OPERATION_CAPABILITY_SECRET_META_API": 48,
    "BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR": 48,
    "BROWSER_AUTHORITY_CONSUMER_TOKEN": 48,
}
DURABLE_KEYS = frozenset(
    {
        "FB_AGENT_BOOTSTRAP_CLUSTER_ID",
        "POSTGRES_PASSWORD",
        "ENCRYPTION_KEY",
        "ENCRYPTION_KEY_VERIFY",
        "API_KEY",
        *GENERATED_SECRETS,
    }
)
BOOTSTRAP_VISION_KEYS = ("VISION_X_TOKEN", "VISION_PROFILE_ID")
BOOTSTRAP_CADDY_KEYS = ("PANEL_BASIC_AUTH_USER", "PANEL_BASIC_AUTH_HASH")
BOOTSTRAP_LEGACY_DROP_KEYS = frozenset(
    {
        "API_HOST",
        "API_PORT",
        "DEV_TOOLS_ENABLED",
        "FRONTEND_ORIGIN",
        "GRPC_PORT",
        "LOG_FORMAT",
        "POSTGRES_HOST",
        "POSTGRES_PORT",
        "REDIS_URL",
        "REQUIRE_API_KEY",
        "SENTRY_ENVIRONMENT",
        "TRACKER_AUTO_CANCEL_ENABLED",
        "TRUST_PROXY_HEADERS",
        "VISION_API_URL",
        "VISION_AUTO_RESTART_ON_MISSING_CDP",
        "VISION_PASSWORD",
        "VISION_TEAM_ID",
        "VISION_USERNAME",
        "WEB_APP_URL",
    }
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
REQUIRED_SOURCE_KEYS = (
    "ENCRYPTION_KEY",
    "ENCRYPTION_KEY_VERIFY",
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_OIDC_CLIENT_ID",
    "TELEGRAM_OIDC_CLIENT_SECRET",
    "API_KEY",
    "DESKTOP_OWNER_TELEGRAM_USER_ID",
)
# Explicit operator-owned source contract.  Compose/runtime-only fields are
# deliberately absent so a stale host export cannot silently alter production.
SOURCE_ALLOWED_KEYS = frozenset(
    """
    FB_AGENT_BOOTSTRAP_CLUSTER_ID POSTGRES_DB POSTGRES_PASSWORD POSTGRES_USER
    ENCRYPTION_KEY ENCRYPTION_KEY_VERIFY API_KEY TELEGRAM_BOT_TOKEN
    TELEGRAM_OIDC_CLIENT_ID TELEGRAM_OIDC_CLIENT_SECRET TELEGRAM_OIDC_REDIRECT_URI
    TELEGRAM_WEBHOOK_SECRET ALERTMANAGER_WEBHOOK_SECRET TMA_SESSION_SECRET
    ADSETPRO_MCP_KEY ADSETPRO_BASE_URL ADSETPRO_TIMEOUT_SECONDS
    ADSETPRO_POSTBACK_SECRET DESKTOP_OWNER_TELEGRAM_USER_ID
    DESKTOP_KASM_SERVICE_USER DESKTOP_KASM_SERVICE_PASSWORD
    BROWSER_MAINTENANCE_CAPABILITY_SECRET
    BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE
    BROWSER_OPERATION_CAPABILITY_SECRET_META_API
    BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR
    BROWSER_AUTHORITY_CONSUMER_TOKEN
    ANTHROPIC_API_KEY ANTHROPIC_BASE_URL ANTHROPIC_MODEL OPENAI_API_KEY
    OPENAI_BASE_URL OPENAI_MODEL AI_DIAGNOSTICS_ENABLED AI_CHAT_ENABLED
    AI_DIAGNOSTICS_COOLDOWN_SECONDS AI_TIMEOUT_SECONDS AI_MAX_LOG_LINES
    AI_MAX_TOOL_ITERATIONS AI_RATE_LIMIT_PER_HOUR AI_PULSE_ENABLED
    AI_PULSE_SLOTS_UTC SYNTX_AUTH_TOKEN SYNTX_BASE_URL SYNTX_TIMEOUT_SECONDS
    SYNTX_POLL_INTERVAL_SECONDS SYNTX_POLL_TIMEOUT_SECONDS
    SYNTX_DEFAULT_IMAGE_AI SYNTX_DEFAULT_IMAGE_MODEL SYNTX_DEFAULT_EDIT_AI
    SYNTX_DEFAULT_EDIT_MODEL SYNTX_DEFAULT_VIDEO_AI SYNTX_DEFAULT_VIDEO_MODEL
    APP_TIMEZONE DEFAULT_OBSERVER_INTERVAL_SECONDS DIGEST_ENABLED DIGEST_HOUR
    DIGEST_TIMEZONE HEALTH_WATCHDOG_ALERT_TTL_SEC
    HEALTH_WATCHDOG_BROWSER_READINESS_SEC HEALTH_WATCHDOG_BROWSER_READINESS_TTL_SEC
    HEALTH_WATCHDOG_INTERVAL_SEC HEALTH_WATCHDOG_META_PROBE_SEC
    HEALTH_WATCHDOG_OBSERVER_STALE_SEC HEALTH_WATCHDOG_STARTUP_GRACE_SEC
    TRACKER_RECONCILIATION_INTERVAL_SECONDS TMA_SESSION_TTL_SECONDS
    TMA_INIT_DATA_MAX_AGE_SECONDS ENABLE_RECO_HOLD_GRACE_SECONDS
    DESKTOP_ACCESS_SESSION_TTL_SECONDS DESKTOP_ACCESS_TICKET_TTL_SECONDS
    PANEL_AUTH_SESSION_TTL_SECONDS PANEL_AUTH_STATE_TTL_SECONDS
    PANEL_AUTH_TICKET_TTL_SECONDS VISION_X_TOKEN VISION_PROFILE_ID
    VISION_FOLDER_ID
    PANEL_BASIC_AUTH_USER PANEL_BASIC_AUTH_HASH
    """.split()
)
RUNTIME_KEYS = frozenset(
    {
        "RUNTIME_ENV_SCHEMA",
        "RELEASE_ID",
        *IMAGE_KEYS,
        "POSTGRES_IMAGE",
        "FB_AGENT_BOOTSTRAP_CLUSTER_ID",
        "POSTGRES_USER",
        "POSTGRES_DB",
        "INFRA_PROJECT_NAME",
        "APP_PROJECT_NAME",
        "DESKTOP_PROJECT_NAME",
        "MONITORING_PROJECT_NAME",
        "PLATFORM_NETWORK",
        "APP_API_PORT",
        "APP_WEB_PORT",
        "APP_TMA_PORT",
        "POSTGRES_HOST_PORT",
        "REDIS_HOST_PORT",
        "DESKTOP_HTTPS_PORT",
        "BROWSER_GRPC_HOST_PORT",
        "PUID",
        "PGID",
        "TZ",
        "PUBLIC_URL",
        "APP_ENV_FILE",
        "DESKTOP_ENV_FILE",
        "BROWSER_CONTROL_ENV_FILE",
        "BROWSER_MAINTENANCE_ENV_FILE",
        "BROWSER_AUTOPAUSE_ENV_FILE",
        "BROWSER_META_API_ENV_FILE",
        "BROWSER_CAMPAIGN_CREATOR_ENV_FILE",
        "BROWSER_AUTHORITY_ENV_FILE",
        "ADOPTION_BUNDLE_FILE",
        "VISION_BOOTSTRAP_ENV_FILE",
        "VISION_CONFIG_DIR",
        "DESKTOP_READINESS_DIR",
        "BROWSER_AUTHORITY_CONSUME_URL",
        "BROWSER_MAINTENANCE_CONSUME_URL",
        "PYTHONDONTWRITEBYTECODE",
    }
)


@dataclass(frozen=True)
class Layout:
    root: Path
    base: Path
    release_id: str

    @classmethod
    def candidate(cls, *, root: Path, release_id: str) -> Layout:
        root = require_absolute_path(root, label="root")
        return cls(root, root / "candidate", require_release_id(release_id))

    @classmethod
    def active(cls, *, root: Path, release_id: str) -> Layout:
        root = require_absolute_path(root, label="root")
        return cls(root, _active_runtime_payload(root), require_release_id(release_id))

    @property
    def shared(self) -> Path:
        return self.root / "shared"

    @property
    def app_env(self) -> Path:
        return self.base / "app.env"

    @property
    def desktop_env(self) -> Path:
        return self.base / "secrets" / "desktop.env"

    @property
    def source_env(self) -> Path:
        return self.base / "source.env"

    @property
    def runtime_env(self) -> Path:
        return self.base / "runtime.env"


@dataclass(frozen=True)
class RuntimeConfig:
    layout: Layout
    values: dict[str, str]
    app_values: dict[str, str]
    desktop_values: dict[str, str]
    docker_config: Path | None = None

    @property
    def api_key(self) -> str:
        return self.app_values["API_KEY"]

    @property
    def public_url(self) -> str:
        return self.values["PUBLIC_URL"]

    def compose_file(self, plane: str) -> Path:
        names = {
            "infra": "docker-compose.infra.yml",
            "jobs": "docker-compose.jobs.yml",
            "app": "docker-compose.app.yml",
            "desktop": "docker-compose.desktop-agent.yml",
        }
        try:
            name = names[plane]
        except KeyError as exc:  # pragma: no cover - private caller invariant
            raise FbctlError(f"unknown Compose plane: {plane}") from exc
        return self.layout.base / "deploy" / "compose" / name

    def project(self, plane: str) -> str:
        keys = {
            "infra": "INFRA_PROJECT_NAME",
            "jobs": "APP_PROJECT_NAME",
            "app": "APP_PROJECT_NAME",
            "desktop": "DESKTOP_PROJECT_NAME",
        }
        return self.values[keys[plane]]

    def compose(self, plane: str, *arguments: str) -> tuple[str, ...]:
        return (
            "docker",
            "compose",
            "-p",
            self.project(plane),
            "--env-file",
            os.fspath(self.layout.runtime_env),
            "-f",
            os.fspath(self.compose_file(plane)),
            *arguments,
        )


def prepare_candidate(
    *,
    root: Path,
    release: dict[str, object],
    source_env: Path | None,
    docker_config: Path | None,
    adoption_bundle: Path | None,
    bootstrap: bool = False,
    rehearsal: bool = False,
) -> RuntimeConfig:
    if release.get("schema") != RELEASE_SCHEMA:
        raise FbctlError("embedded release descriptor has an unsupported schema")
    release_id = require_release_id(str(release.get("release_id", "")))
    images = release.get("images")
    if not isinstance(images, dict) or set(images) != set(IMAGE_KEYS):
        raise FbctlError("embedded release descriptor has an invalid image set")
    layout = Layout.candidate(root=root, release_id=release_id)
    require_directory(layout.shared, mode=0o700)
    require_directory(layout.base, mode=0o700)
    verify_materialized_resources(layout.base)

    selected_source = source_env or layout.shared / "source.env"
    require_private_file(selected_source)
    incumbent_path = layout.shared / "source.env"
    incumbent = parse_dotenv(incumbent_path) if incumbent_path.is_file() else {}
    raw_source = parse_dotenv(selected_source)
    vision_bootstrap: dict[str, str] | None = None
    if bootstrap:
        missing = [key for key in BOOTSTRAP_VISION_KEYS if not raw_source.get(key)]
        if missing:
            raise FbctlError(f"bootstrap source environment is missing required {missing[0]}")
        token = raw_source["VISION_X_TOKEN"].strip()
        profile_id = raw_source["VISION_PROFILE_ID"].strip()
        if (
            not token
            or len(token) > 16_384
            or "\r" in token
            or "\n" in token
            or not re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", profile_id)
        ):
            raise FbctlError("bootstrap Vision credentials are invalid")
        vision_bootstrap = {
            "VISION_BOOTSTRAP_X_TOKEN": token,
            "VISION_BOOTSTRAP_PROFILE_ID": profile_id,
        }
    source_values = canonicalize_source(raw_source, incumbent=incumbent)
    atomic_write(layout.source_env, render_dotenv(source_values), mode=0o600)

    app_values = dict(source_values)
    for key in PRIVATE_BROWSER_KEYS:
        app_values.pop(key, None)
    # Vision gets exactly the credentials it needs.  It must never inherit the
    # API/database/Telegram/AI environment used by application containers.
    desktop_values = {
        key: source_values[key]
        for key in ("DESKTOP_KASM_SERVICE_USER", "DESKTOP_KASM_SERVICE_PASSWORD")
    }
    app_values.update(
        {
            "FRONTEND_ORIGIN": PUBLIC_URL,
            "WEB_APP_URL": f"{PUBLIC_URL}/tma/",
            "DESKTOP_PUBLIC_ORIGIN": "https://desktop.adpulse.su",
            "BROWSER_AUTHORITY_CONSUME_URL": (
                "https://app.adpulse.su/api/v1/internal/browser-operations/consume"
            ),
            "BROWSER_MAINTENANCE_CONSUME_URL": (
                "https://app.adpulse.su/api/v1/internal/browser-maintenance/consume"
            ),
            "REQUIRE_API_KEY": "true",
            "TRUST_PROXY_HEADERS": "true",
            "DEV_TOOLS_ENABLED": "false",
            "LOG_FORMAT": "json",
            "DEPLOYMENT_ENVIRONMENT": "rehearsal" if rehearsal else "production",
            "DESKTOP_WEBTOP_IMAGE": str(images["DESKTOP_WEBTOP_IMAGE"]),
        }
    )
    if rehearsal:
        app_values["TELEGRAM_BOT_API_ORIGIN"] = "http://telegram-stub:18080"
    validate_app_values(app_values)
    atomic_write(layout.app_env, render_dotenv(app_values), mode=0o600)
    atomic_write(layout.desktop_env, render_dotenv(desktop_values), mode=0o600)
    browser_paths = write_scoped_browser_environments(layout, source_values)
    vision_bootstrap_path: Path | None = None
    if vision_bootstrap is not None:
        vision_bootstrap_path = layout.base / "secrets" / "vision-bootstrap.env"
        atomic_write(vision_bootstrap_path, render_dotenv(vision_bootstrap), mode=0o600)
    require_directory(layout.shared / "vision-config")

    values = {
        "RUNTIME_ENV_SCHEMA": RUNTIME_SCHEMA,
        "RELEASE_ID": release_id,
        **{key: str(images[key]) for key in IMAGE_KEYS},
        "POSTGRES_IMAGE": POSTGRES_IMAGE,
        "FB_AGENT_BOOTSTRAP_CLUSTER_ID": source_values["FB_AGENT_BOOTSTRAP_CLUSTER_ID"],
        "POSTGRES_USER": source_values["POSTGRES_USER"],
        "POSTGRES_DB": source_values["POSTGRES_DB"],
        "INFRA_PROJECT_NAME": INFRA_PROJECT_NAME,
        "APP_PROJECT_NAME": "fb_agent_app",
        "DESKTOP_PROJECT_NAME": "fb_agent_desktop",
        "MONITORING_PROJECT_NAME": "fb_agent_monitoring",
        "PLATFORM_NETWORK": "fb_agent_platform",
        "APP_API_PORT": "18100",
        "APP_WEB_PORT": "18080",
        "APP_TMA_PORT": "18081",
        **dict(INFRA_HOST_PORTS),
        "DESKTOP_HTTPS_PORT": "8444",
        "BROWSER_GRPC_HOST_PORT": "50051",
        "PUID": "1000",
        "PGID": "1000",
        "TZ": "Europe/Kaliningrad",
        "PUBLIC_URL": PUBLIC_URL,
        "APP_ENV_FILE": os.fspath(layout.app_env),
        "DESKTOP_ENV_FILE": os.fspath(layout.desktop_env),
        **{key: os.fspath(path) for key, path in browser_paths.items()},
        "ADOPTION_BUNDLE_FILE": (
            os.fspath(adoption_bundle) if adoption_bundle is not None else "/dev/null"
        ),
        "VISION_BOOTSTRAP_ENV_FILE": (
            os.fspath(vision_bootstrap_path) if vision_bootstrap_path is not None else "/dev/null"
        ),
        "VISION_CONFIG_DIR": os.fspath(layout.shared / "vision-config"),
        "DESKTOP_READINESS_DIR": os.fspath(layout.shared / "desktop-readiness"),
        "BROWSER_AUTHORITY_CONSUME_URL": app_values["BROWSER_AUTHORITY_CONSUME_URL"],
        "BROWSER_MAINTENANCE_CONSUME_URL": app_values["BROWSER_MAINTENANCE_CONSUME_URL"],
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    atomic_write(layout.runtime_env, render_dotenv(values), mode=0o400)
    return RuntimeConfig(layout, values, app_values, desktop_values, docker_config)


def load_active(root: Path, *, docker_config: Path | None = None) -> RuntimeConfig:
    root = require_absolute_path(root, label="root")
    runtime = _active_runtime_payload(root)
    runtime_env = require_private_file(runtime / "runtime.env", mode=0o400)
    values = parse_dotenv(runtime_env)
    if set(values) != RUNTIME_KEYS:
        raise FbctlError("active runtime environment does not match the exact key contract")
    if values.get("RUNTIME_ENV_SCHEMA") != RUNTIME_SCHEMA:
        raise FbctlError("active runtime uses an unsupported schema")
    release_id = require_release_id(values.get("RELEASE_ID", ""))
    layout = Layout.active(root=root, release_id=release_id)
    app_env = Path(values.get("APP_ENV_FILE", ""))
    if app_env != layout.app_env:
        raise FbctlError("active runtime points to a non-canonical app environment")
    require_private_file(app_env)
    desktop_env = Path(values.get("DESKTOP_ENV_FILE", ""))
    if desktop_env != layout.desktop_env:
        raise FbctlError("active runtime points to a non-canonical desktop environment")
    require_private_file(desktop_env)
    expected_paths = {
        "BROWSER_CONTROL_ENV_FILE": layout.base / "secrets" / "browser-control.env",
        "BROWSER_MAINTENANCE_ENV_FILE": layout.base / "secrets" / "browser-maintenance.env",
        "BROWSER_AUTOPAUSE_ENV_FILE": layout.base / "secrets" / "browser-autopause.env",
        "BROWSER_META_API_ENV_FILE": layout.base / "secrets" / "browser-meta-api.env",
        "BROWSER_CAMPAIGN_CREATOR_ENV_FILE": layout.base
        / "secrets"
        / "browser-campaign-creator.env",
        "BROWSER_AUTHORITY_ENV_FILE": layout.base / "secrets" / "browser-authority.env",
        "VISION_CONFIG_DIR": layout.shared / "vision-config",
        "DESKTOP_READINESS_DIR": layout.shared / "desktop-readiness",
    }
    for key, expected_path in expected_paths.items():
        if Path(values[key]) != expected_path:
            raise FbctlError(f"active runtime points {key} outside its canonical path")
    if (
        values["ADOPTION_BUNDLE_FILE"] != "/dev/null"
        or values["VISION_BOOTSTRAP_ENV_FILE"] != "/dev/null"
    ):
        raise FbctlError("active runtime retains bootstrap-only file transport")
    for key in IMAGE_KEYS:
        if not IMAGE_DIGEST.fullmatch(values[key]):
            raise FbctlError(f"active runtime image is not immutable: {key}")
    app_values = parse_dotenv(app_env, required=("API_KEY",))
    desktop_values = parse_dotenv(
        desktop_env,
        required=("DESKTOP_KASM_SERVICE_USER", "DESKTOP_KASM_SERVICE_PASSWORD"),
    )
    if set(desktop_values) != {
        "DESKTOP_KASM_SERVICE_USER",
        "DESKTOP_KASM_SERVICE_PASSWORD",
    }:
        raise FbctlError("active desktop environment does not match the exact key contract")
    return RuntimeConfig(layout, values, app_values, desktop_values, docker_config)


def _active_runtime_payload(root: Path) -> Path:
    """Resolve the one active payload without admitting a mutable directory slot.

    ``runtime`` is a relative symlink switched by one ``os.replace``.  Its
    target is a private, sibling payload prepared before that replace; this is
    the commit point for a release.
    """
    pointer = root / "runtime"
    try:
        metadata = pointer.lstat()
    except OSError as exc:
        raise FbctlError("active runtime pointer is missing") from exc
    if not stat.S_ISLNK(metadata.st_mode):
        raise FbctlError("active runtime must be an atomic fbctl pointer")
    try:
        raw_target = os.readlink(pointer)
    except OSError as exc:
        raise FbctlError("active runtime pointer is unreadable") from exc
    target = Path(raw_target)
    if (
        target.is_absolute()
        or target.parent != Path(".")
        or not target.name.startswith(".runtime-")
    ):
        raise FbctlError("active runtime pointer has an unsafe target")
    payload = root / target
    require_directory(payload, mode=0o700)
    return payload


def canonicalize_source(values: dict[str, str], *, incumbent: dict[str, str]) -> dict[str, str]:
    unknown = sorted(set(values) - SOURCE_ALLOWED_KEYS)
    if unknown:
        raise FbctlError(f"source environment contains unsupported key {unknown[0]}")
    result = dict(values)
    # Vision credentials are bootstrap transport only.  The canonical token is
    # encrypted in PostgreSQL and plaintext must never survive in source/app env.
    for key in (*BOOTSTRAP_VISION_KEYS, *BOOTSTRAP_CADDY_KEYS):
        result.pop(key, None)
    result.setdefault("POSTGRES_USER", "fb_agent")
    result.setdefault("POSTGRES_DB", "fb_agent")
    result.setdefault("DESKTOP_KASM_SERVICE_USER", "adpulse-desktop")
    result.setdefault("TELEGRAM_OIDC_REDIRECT_URI", f"{PUBLIC_URL}/auth/telegram/callback")
    for key in DURABLE_KEYS:
        old_value = incumbent.get(key, "")
        new_value = result.get(key, "")
        if old_value and new_value and old_value != new_value:
            raise FbctlError(f"source environment attempts to rotate durable {key}")
        if old_value and not new_value:
            result[key] = old_value
    if not result.get("FB_AGENT_BOOTSTRAP_CLUSTER_ID"):
        result["FB_AGENT_BOOTSTRAP_CLUSTER_ID"] = uuid.uuid4().hex
    password = result.get("POSTGRES_PASSWORD", "")
    if not password or password in {"fb_stop_bot", result["POSTGRES_DB"]}:
        result["POSTGRES_PASSWORD"] = secrets.token_urlsafe(48)
    for key, minimum in GENERATED_SECRETS.items():
        if not result.get(key):
            result[key] = secrets.token_urlsafe(max(48, minimum))
    missing = [key for key in REQUIRED_SOURCE_KEYS if not result.get(key)]
    if missing:
        raise FbctlError(f"source environment is missing required {missing[0]}")
    validate_source_values(result)
    return result


def project_bootstrap_source(
    values: dict[str, str],
    *,
    project_known_legacy_source: bool,
) -> tuple[dict[str, str], tuple[str, ...]]:
    """Project one reviewed legacy source shape before *bootstrap* only.

    This is intentionally not part of :func:`canonicalize_source`: routine
    deploys and callers that canonicalize configuration directly remain strict.
    Unknown names are reported together, never with their values.
    """

    result = dict(values)
    dropped: list[str] = []
    if project_known_legacy_source:
        for key in BOOTSTRAP_LEGACY_DROP_KEYS:
            if key in result:
                result.pop(key)
                dropped.append(key)
        if "TELEGRAM_CHAT_ID" in result:
            result.pop("TELEGRAM_CHAT_ID")
            dropped.append("TELEGRAM_CHAT_ID")
    unknown = sorted(set(result) - SOURCE_ALLOWED_KEYS)
    if unknown:
        raise FbctlError("source environment contains unsupported keys: " + ", ".join(unknown))
    return result, tuple(sorted(dropped))


def parse_bootstrap_source_stdin(payload: bytes) -> dict[str, str]:
    """Parse the same strict dotenv grammar as ``parse_dotenv`` without I/O."""

    if not payload or len(payload) > 2_000_000 or b"\x00" in payload:
        raise FbctlError("source environment stdin is empty or exceeds 2 MB")
    return parse_dotenv_payload(payload, label="stdin", maximum=2_000_000)


def validate_bootstrap_source_check(values: dict[str, str]) -> None:
    """Validate bootstrap-only material without reading host state or writing files."""

    missing = [key for key in BOOTSTRAP_VISION_KEYS if not values.get(key)]
    if missing:
        raise FbctlError(f"bootstrap source environment is missing required {missing[0]}")
    token = values["VISION_X_TOKEN"].strip()
    profile_id = values["VISION_PROFILE_ID"].strip()
    if (
        not token
        or len(token) > 16_384
        or "\r" in token
        or "\n" in token
        or not re.fullmatch(r"[A-Za-z0-9._:-]{1,64}", profile_id)
    ):
        raise FbctlError("bootstrap Vision credentials are invalid")
    present = [key for key in BOOTSTRAP_CADDY_KEYS if key in values]
    if len(present) == 1:
        raise FbctlError("Caddy bootstrap credentials must provide both panel keys or neither")
    if len(present) == 2:
        user = values["PANEL_BASIC_AUTH_USER"]
        password_hash = values["PANEL_BASIC_AUTH_HASH"]
        if not re.fullmatch(r"[A-Za-z0-9._@-]{1,64}", user) or not re.fullmatch(
            r"\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}", password_hash
        ):
            raise FbctlError("Caddy panel credentials are invalid")


def validate_source_values(values: dict[str, str]) -> None:
    if not re.fullmatch(r"[0-9a-f]{32}", values.get("FB_AGENT_BOOTSTRAP_CLUSTER_ID", "")):
        raise FbctlError("source environment has an invalid cluster id")
    if len(values.get("POSTGRES_PASSWORD", "")) < 16:
        raise FbctlError("source environment has an invalid PostgreSQL password")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,62}", values.get("POSTGRES_USER", "")):
        raise FbctlError("source environment has an invalid PostgreSQL user")
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,62}", values.get("POSTGRES_DB", "")):
        raise FbctlError("source environment has an invalid PostgreSQL database")
    if len(values.get("API_KEY", "")) < 24:
        raise FbctlError("source environment has an invalid API key")
    try:
        if len(base64.urlsafe_b64decode(values.get("ENCRYPTION_KEY", ""))) != 32:
            raise ValueError
    except (ValueError, TypeError) as exc:
        raise FbctlError("source environment has an invalid encryption key") from exc
    if values.get("TELEGRAM_OIDC_REDIRECT_URI", f"{PUBLIC_URL}/auth/telegram/callback") != (
        f"{PUBLIC_URL}/auth/telegram/callback"
    ):
        raise FbctlError("Telegram OIDC redirect URI is not canonical")
    if not values.get("TELEGRAM_OIDC_CLIENT_ID", "").isdigit():
        raise FbctlError("Telegram OIDC client id must be numeric")
    if len(values.get("TELEGRAM_OIDC_CLIENT_SECRET", "")) < 32:
        raise FbctlError("Telegram OIDC client secret is too short")
    try:
        owner_id = int(values.get("DESKTOP_OWNER_TELEGRAM_USER_ID", "0"))
    except ValueError:
        owner_id = 0
    if owner_id <= 0:
        raise FbctlError("desktop owner Telegram id must be positive")
    vision_folder_id = values.get("VISION_FOLDER_ID", "")
    if vision_folder_id and not re.fullmatch(r"[A-Za-z0-9._:-]{1,128}", vision_folder_id):
        raise FbctlError("source environment has an invalid Vision folder id")
    for key, minimum in GENERATED_SECRETS.items():
        if len(values.get(key, "")) < minimum:
            raise FbctlError(f"source environment has an invalid {key}")


def validate_app_values(values: dict[str, str]) -> None:
    expected = {
        "REQUIRE_API_KEY": "true",
        "TRUST_PROXY_HEADERS": "true",
        "DEV_TOOLS_ENABLED": "false",
        "LOG_FORMAT": "json",
        "FRONTEND_ORIGIN": PUBLIC_URL,
        "WEB_APP_URL": f"{PUBLIC_URL}/tma/",
    }
    for key, expected_value in expected.items():
        if values.get(key) != expected_value:
            raise FbctlError(f"candidate app environment has invalid {key}")
    deployment_environment = values.get("DEPLOYMENT_ENVIRONMENT")
    if deployment_environment not in {"production", "rehearsal"}:
        raise FbctlError("candidate app environment has invalid DEPLOYMENT_ENVIRONMENT")
    rehearsal_origin = values.get("TELEGRAM_BOT_API_ORIGIN")
    if deployment_environment == "rehearsal":
        if rehearsal_origin != "http://telegram-stub:18080":
            raise FbctlError("rehearsal Telegram Bot API origin is not canonical")
    elif rehearsal_origin:
        raise FbctlError("production cannot override the Telegram Bot API origin")
    if len(values.get("API_KEY", "")) < 24:
        raise FbctlError("candidate app environment has an invalid API key")


def write_scoped_browser_environments(
    layout: Layout,
    source: dict[str, str],
) -> dict[str, Path]:
    directory = layout.base / "secrets"
    directory.mkdir(mode=0o700, exist_ok=True)
    mapping = {
        "BROWSER_CONTROL_ENV_FILE": (
            "browser-control.env",
            {
                key: source[key]
                for key in (
                    "BROWSER_MAINTENANCE_CAPABILITY_SECRET",
                    "BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE",
                    "BROWSER_OPERATION_CAPABILITY_SECRET_META_API",
                    "BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR",
                    "BROWSER_AUTHORITY_CONSUMER_TOKEN",
                )
            },
        ),
        "BROWSER_MAINTENANCE_ENV_FILE": (
            "browser-maintenance.env",
            {
                "BROWSER_MAINTENANCE_CAPABILITY_SECRET": source[
                    "BROWSER_MAINTENANCE_CAPABILITY_SECRET"
                ]
            },
        ),
        "BROWSER_AUTOPAUSE_ENV_FILE": (
            "browser-autopause.env",
            {
                "BROWSER_OPERATION_CAPABILITY_SECRET": source[
                    "BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE"
                ]
            },
        ),
        "BROWSER_META_API_ENV_FILE": (
            "browser-meta-api.env",
            {
                "BROWSER_OPERATION_CAPABILITY_SECRET": source[
                    "BROWSER_OPERATION_CAPABILITY_SECRET_META_API"
                ]
            },
        ),
        "BROWSER_CAMPAIGN_CREATOR_ENV_FILE": (
            "browser-campaign-creator.env",
            {
                "BROWSER_OPERATION_CAPABILITY_SECRET": source[
                    "BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR"
                ]
            },
        ),
        "BROWSER_AUTHORITY_ENV_FILE": (
            "browser-authority.env",
            {"BROWSER_AUTHORITY_CONSUMER_TOKEN": source["BROWSER_AUTHORITY_CONSUMER_TOKEN"]},
        ),
    }
    result: dict[str, Path] = {}
    for runtime_key, (name, values) in mapping.items():
        path = directory / name
        atomic_write(path, render_dotenv(values), mode=0o600)
        result[runtime_key] = path
    return result


def render_dotenv(values: dict[str, str]) -> bytes:
    for key, value in values.items():
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", key):
            raise FbctlError("cannot render an invalid dotenv key")
        if "\r" in value or "\n" in value:
            raise FbctlError(f"cannot render multiline dotenv value: {key}")
    return "".join(f"{key}={values[key]}\n" for key in sorted(values)).encode("utf-8")
