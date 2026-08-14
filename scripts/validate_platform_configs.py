#!/usr/bin/env python3
"""Read-only validation of the fbctl zipapp and embedded Compose contract."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, os.fspath(ROOT))

from fbctl.bundle import PREFLIGHT_MODULES, RESOURCE_FILES, inspect_bundle  # noqa: E402

IMMUTABLE_FROM = re.compile(r"@sha256:[0-9a-f]{64}(?:\s|$)")
DOCKERFILES = (
    "docker/Dockerfile.python-base",
    "docker/Dockerfile.browser-agent",
    "docker/Dockerfile.frontend",
    "docker/Dockerfile.mini-app",
    "deploy/vision-webtop/Dockerfile",
)


class ValidationError(RuntimeError):
    """The release control plane is internally inconsistent."""


def _run(arguments: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=ROOT,
        check=check,
        capture_output=True,
        text=True,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def _write(path: Path, content: str, mode: int = 0o600) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)


def _release_manifest() -> str:
    image = "ghcr.io/example/fb-agent@sha256:" + "a" * 64
    images = {
        key: image
        for key in (
            "API_IMAGE",
            "WORKERS_IMAGE",
            "FRONTEND_IMAGE",
            "MINI_APP_IMAGE",
            "BROWSER_AGENT_IMAGE",
            "DESKTOP_WEBTOP_IMAGE",
            "REDIS_IMAGE",
        )
    }
    return (
        json.dumps(
            {"schema": "fb-agent-release/v1", "release_id": "validation", "images": images},
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    )


def _source_environment() -> str:
    return "\n".join(
        (
            "ENCRYPTION_KEY=UOGaDCkFFfSv7XMSdwQq_rqmossFFl8wSG7z69_5nO0=",
            "ENCRYPTION_KEY_VERIFY=gAAAAABqZwkRi9J37pVDxsdD0LHKWe_L6EkbhQVu1yKi_N43MdYL_I1IV_-5gsOOBXzCRMY9phj3dpLhDtQCsDcJPQKhEQjiRNeb6RuubyvM6vuxf6dgr30=",
            "TELEGRAM_BOT_TOKEN=123456789:validation-only-token",
            "TELEGRAM_OIDC_CLIENT_ID=123456789",
            "TELEGRAM_OIDC_CLIENT_SECRET=validation-only-oidc-secret-0123456789abcdef",
            "API_KEY=validation-only-api-key-0123456789",
            "DESKTOP_OWNER_TELEGRAM_USER_ID=1",
            "VISION_X_TOKEN=validation-only-vision-token",
            "VISION_PROFILE_ID=validation-profile",
            "",
        )
    )


def _validate_zipapp_preflight() -> None:
    with tempfile.TemporaryDirectory(prefix="fb-agent-platform-validation-") as directory:
        temporary = Path(directory)
        source = temporary / "source.env"
        manifest = temporary / "release.json"
        bundle = temporary / "fbctl.pyz"
        preflight_bundle = temporary / "fbctl-preflight.pyz"
        _write(source, _source_environment())
        _write(manifest, _release_manifest())
        _run(
            [
                os.fspath(ROOT / "scripts/fbctl"),
                "bundle",
                "--output",
                os.fspath(bundle),
                "--release-id",
                "validation",
                "--release-manifest",
                os.fspath(manifest),
                "--source-root",
                os.fspath(ROOT),
            ]
        )
        _run([sys.executable, "-B", os.fspath(bundle), "--help"])
        inspect_bundle(bundle)
        _run(
            [
                os.fspath(ROOT / "scripts/fbctl"),
                "preflight-bundle",
                "--output",
                os.fspath(preflight_bundle),
                "--release-id",
                "validation",
                "--source-root",
                os.fspath(ROOT),
            ]
        )
        _run([sys.executable, "-B", os.fspath(preflight_bundle), "--help"])
        inspect_bundle(preflight_bundle)
        with zipfile.ZipFile(preflight_bundle) as archive:
            expected = {
                "__main__.py",
                "fbctl/resources/artifact-manifest.json",
                *(f"fbctl/{name}" for name in PREFLIGHT_MODULES),
            }
            if set(archive.namelist()) != expected:
                raise ValidationError("preflight zipapp contains files outside its allowlist")
        with zipfile.ZipFile(bundle) as archive:
            for archived, relative in RESOURCE_FILES.items():
                if archive.read(f"fbctl/resources/{archived}") != (ROOT / relative).read_bytes():
                    raise ValidationError(f"zipapp resource differs from source: {relative}")
        runtime = temporary / "runtime.env"
        image = "ghcr.io/example/fb-agent@sha256:" + "a" * 64
        values = {
            "RELEASE_ID": "validation",
            "FB_AGENT_BOOTSTRAP_CLUSTER_ID": "a" * 32,
            "POSTGRES_USER": "fb_agent",
            "POSTGRES_DB": "fb_agent",
            "INFRA_PROJECT_NAME": "fb_agent_infra",
            "APP_PROJECT_NAME": "fb_agent_app",
            "DESKTOP_PROJECT_NAME": "fb_agent_desktop",
            "PLATFORM_NETWORK": "fb_agent_platform",
            "APP_API_PORT": "18100",
            "APP_WEB_PORT": "18080",
            "APP_TMA_PORT": "18081",
            "POSTGRES_HOST_PORT": "5433",
            "REDIS_HOST_PORT": "6380",
            "DESKTOP_HTTPS_PORT": "8444",
            "BROWSER_GRPC_HOST_PORT": "50051",
            "APP_ENV_FILE": os.fspath(source),
            "DESKTOP_ENV_FILE": os.fspath(source),
            "ADOPTION_BUNDLE_FILE": os.fspath(source),
            "VISION_BOOTSTRAP_ENV_FILE": os.fspath(source),
            "VISION_CONFIG_DIR": os.fspath(temporary),
            "DESKTOP_READINESS_DIR": os.fspath(temporary),
            "BROWSER_CONTROL_ENV_FILE": os.fspath(source),
            "BROWSER_AUTHORITY_ENV_FILE": os.fspath(source),
            "BROWSER_MAINTENANCE_ENV_FILE": os.fspath(source),
            "BROWSER_AUTOPAUSE_ENV_FILE": os.fspath(source),
            "BROWSER_META_API_ENV_FILE": os.fspath(source),
            "BROWSER_CAMPAIGN_CREATOR_ENV_FILE": os.fspath(source),
            "BROWSER_AUTHORITY_CONSUME_URL": "https://app.example/internal/consume",
            "BROWSER_MAINTENANCE_CONSUME_URL": "https://app.example/internal/maintenance",
            "API_IMAGE": image,
            "WORKERS_IMAGE": image,
            "FRONTEND_IMAGE": image,
            "MINI_APP_IMAGE": image,
            "BROWSER_AGENT_IMAGE": image,
            "DESKTOP_WEBTOP_IMAGE": image,
            "POSTGRES_IMAGE": image,
            "REDIS_IMAGE": image,
        }
        _write(runtime, "".join(f"{key}={value}\n" for key, value in sorted(values.items())))
        planes = {
            "infra": "docker-compose.infra.yml",
            "jobs": "docker-compose.jobs.yml",
            "desktop": "docker-compose.desktop-agent.yml",
            "app": "docker-compose.app.yml",
        }
        for plane, name in planes.items():
            arguments = [
                "docker",
                "compose",
                "-p",
                values[
                    "APP_PROJECT_NAME"
                    if plane in {"jobs", "app"}
                    else f"{plane.upper()}_PROJECT_NAME"
                ],
                "--env-file",
                os.fspath(runtime),
                "-f",
                os.fspath(ROOT / "deploy/compose" / name),
            ]
            if plane == "app":
                arguments.extend(("--profile", "workers"))
            _run([*arguments, "config", "--quiet"])


def _validate_python_sources() -> None:
    for path in sorted((ROOT / "fbctl").glob("*.py")) + [
        ROOT / "scripts/ci_image_plan.py",
        Path(__file__),
    ]:
        compile(path.read_text(encoding="utf-8"), os.fspath(path), "exec")


def _validate_immutable_bases() -> None:
    for relative in DOCKERFILES:
        for line in (ROOT / relative).read_text(encoding="utf-8").splitlines():
            if line.startswith("FROM ") and not IMMUTABLE_FROM.search(line):
                raise ValidationError(f"{relative} contains a non-immutable base")


def _validate_no_promtail() -> None:
    monitoring = ROOT / "deploy/monitoring"
    for path in monitoring.rglob("*"):
        if not path.is_file() or path.name == "README.md":
            continue
        if re.search(r"\bpromtail\b", path.read_text(encoding="utf-8", errors="ignore")):
            raise ValidationError(f"Promtail runtime configuration exists in {path}")


def validate() -> None:
    for command in ("docker",):
        if shutil.which(command) is None:
            raise ValidationError(f"{command} is not installed")
    _run(["docker", "compose", "version"])
    _validate_python_sources()
    _validate_immutable_bases()
    _validate_no_promtail()
    _validate_zipapp_preflight()


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser().parse_args(argv)
    try:
        validate()
    except (OSError, subprocess.CalledProcessError, ValidationError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print("Platform configuration validation: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
