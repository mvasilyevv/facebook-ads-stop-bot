"""Routine operator commands over the active single-slot runtime."""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import time
from pathlib import Path

from fbctl.bundle import (
    BUNDLE_SCHEMA,
    inspect_bundle,
    read_release_manifest,
    verify_materialized_resources,
)
from fbctl.config import (
    SOURCE_ALLOWED_KEYS,
    RuntimeConfig,
    load_active,
    validate_source_values,
)
from fbctl.controller import (
    DESKTOP_SERVICES,
    RESTART_SERVICES,
    WORKERS,
    ProductionController,
)
from fbctl.errors import FbctlError
from fbctl.files import (
    MAX_DOTENV_BYTES,
    parse_dotenv_payload,
    require_absolute_path,
    snapshot_private_file,
    trusted_shared_directory,
)
from fbctl.probes import (
    ProbeClient,
    UrllibProbeClient,
    require_exact_browser,
    require_ok_status,
    require_operator_snapshot,
    require_system_ready,
    require_telegram_webhook,
    wait_for,
)
from fbctl.runner import CommandRunner, SubprocessRunner, sealed_process_environment


def doctor(
    *,
    root: Path,
    runner: CommandRunner | None = None,
    probes: ProbeClient | None = None,
    docker_config: Path | None = None,
) -> dict[str, object]:
    runner = runner or SubprocessRunner()
    probes = probes or UrllibProbeClient()
    root = require_absolute_path(root, label="root")
    checks: dict[str, str] = {}
    errors: list[str] = []
    if sys.version_info < (3, 12):
        errors.append("python_3_12_required")
    else:
        checks["python"] = sys.version.split()[0]
    try:
        with trusted_shared_directory(root, required_uid=os.geteuid()) as shared_fd:
            assert shared_fd is not None
            source_snapshot = snapshot_private_file(
                root / "shared" / "source.env",
                label="canonical source environment",
                maximum=MAX_DOTENV_BYTES,
                required_uid=os.geteuid(),
                directory_fd=shared_fd,
            )
            assert source_snapshot is not None
        source_values = parse_dotenv_payload(
            source_snapshot.payload,
            label="canonical source environment",
        )
        unknown = sorted(set(source_values) - SOURCE_ALLOWED_KEYS)
        if unknown:
            raise FbctlError(f"source environment contains unsupported key {unknown[0]}")
        validate_source_values(source_values)
        checks["source_config"] = "ready"
    except FbctlError as exc:
        errors.append(str(exc))
    result = runner.run(
        ("docker", "compose", "version"),
        step="doctor",
        env=sealed_process_environment(docker_config=docker_config),
        capture=True,
        check=False,
    )
    if result.returncode:
        errors.append("docker_compose_unavailable")
    else:
        checks["docker_compose"] = "ready"
    try:
        config = load_active(root, docker_config=docker_config)
    except FbctlError as exc:
        checks["runtime"] = "not_deployed"
        errors.append(f"active_runtime_unavailable: {exc}")
    else:
        checks["runtime"] = config.layout.release_id
        try:
            verify_materialized_resources(config.layout.base)
            bundle = inspect_bundle(config.layout.base / "fbctl.pyz")
            if bundle.schema != BUNDLE_SCHEMA:
                raise FbctlError("active control bundle is not a full release bundle")
            if bundle.release_id != config.layout.release_id:
                raise FbctlError("active control bundle belongs to another release")
            read_release_manifest(
                config.layout.base / "release.json",
                expected_release_id=config.layout.release_id,
            )
            checks["release_assets"] = "ready"
            _validate_runtime_ports(config)
            checks["ports"] = "ready"
            for plane in ("infra", "jobs", "desktop"):
                runner.run(
                    config.compose(plane, "config", "--quiet"),
                    step="doctor",
                    env=sealed_process_environment(docker_config=docker_config),
                )
            runner.run(
                config.compose("app", "--profile", "workers", "config", "--quiet"),
                step="doctor",
                env=sealed_process_environment(docker_config=docker_config),
            )
            ProductionController(runner=runner)._require_managed_resources(  # noqa: SLF001
                config,
                include_campaign=True,
            )
            checks["compose_inventory"] = "ready"
            controller = ProductionController(runner=runner)
            controller._require_caddy_credentials(config)  # noqa: SLF001
            runner.run(
                (
                    "caddy",
                    "validate",
                    "--config",
                    "/etc/caddy/Caddyfile",
                    "--adapter",
                    "caddyfile",
                    "--envfile",
                    "/etc/fb-agent/caddy.env",
                ),
                step="doctor",
            )
            checks["caddy"] = "ready"
            require_ok_status(probes, f"{_api_origin(config)}/healthz")
            checks["api"] = "ready"
        except FbctlError as exc:
            errors.append(str(exc))
    free_bytes = shutil.disk_usage(root).free
    if free_bytes < 5 * 1024**3:
        errors.append("less_than_5_gib_free")
    else:
        checks["disk"] = "ready"
    return {"status": "READY" if not errors else "FAILED", "checks": checks, "errors": errors}


def status(
    *,
    root: Path,
    runner: CommandRunner | None = None,
    probes: ProbeClient | None = None,
    docker_config: Path | None = None,
) -> dict[str, object]:
    runner = runner or SubprocessRunner()
    probes = probes or UrllibProbeClient()
    config = load_active(root, docker_config=docker_config)
    environment = sealed_process_environment(docker_config=docker_config)
    planes: dict[str, object] = {}
    for plane in ("infra", "desktop", "app"):
        arguments = (
            ("--profile", "workers", "ps", "--format", "json")
            if plane == "app"
            else (
                "ps",
                "--format",
                "json",
            )
        )
        result = runner.run(
            config.compose(plane, *arguments),
            step="status",
            env=environment,
            capture=True,
            check=False,
        )
        planes[plane] = _parse_compose_ps(result.stdout) if result.returncode == 0 else []
    checks: dict[str, bool] = {}
    failures: list[str] = []
    for name, check in (
        ("api", lambda: require_ok_status(probes, f"{_api_origin(config)}/readyz")),
        (
            "snapshot",
            lambda: require_operator_snapshot(probes, _api_origin(config), config.api_key),
        ),
        (
            "browser",
            lambda: require_exact_browser(probes, _api_origin(config), config.api_key),
        ),
        ("system", lambda: require_system_ready(probes, _api_origin(config))),
        (
            "telegram",
            lambda: require_telegram_webhook(probes, _api_origin(config), config.api_key),
        ),
    ):
        try:
            check()
            checks[name] = True
        except FbctlError:
            checks[name] = False
            failures.append(name)
    controller = ProductionController(runner=runner, probes=probes)
    try:
        controller._verify_worker_heartbeats(  # noqa: SLF001 - shared evidence gate
            config,
            timeout=10,
        )
        checks["worker_heartbeats"] = True
    except FbctlError:
        checks["worker_heartbeats"] = False
        failures.append("worker_heartbeats")
    # Deployment evidence is part of the immutable payload selected by the
    # runtime pointer, so status never depends on a post-commit shared write.
    state_path = config.layout.base / "deploy-state.json"
    try:
        deployment = json.loads(state_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, UnicodeError):
        deployment = None
    return {
        "status": "READY" if not failures else "DEGRADED",
        "release_id": config.layout.release_id,
        "checks": checks,
        "failures": failures,
        "planes": planes,
        "deployment": deployment,
    }


def logs(
    *,
    root: Path,
    service: str,
    lines: int,
    follow: bool,
    runner: CommandRunner | None = None,
    docker_config: Path | None = None,
) -> None:
    if service not in RESTART_SERVICES:
        raise FbctlError("service is not in the fbctl allowlist")
    if lines < 1 or lines > 10_000:
        raise FbctlError("lines must be between 1 and 10000")
    runner = runner or SubprocessRunner()
    config = load_active(root, docker_config=docker_config)
    plane = "desktop" if service in DESKTOP_SERVICES else "app"
    prefix = ("--profile", "workers") if service in WORKERS else ()
    arguments = (*prefix, "logs", "--tail", str(lines), "--timestamps")
    if follow:
        arguments = (*arguments, "--follow")
    runner.run(
        config.compose(plane, *arguments, service),
        step="logs",
        env=sealed_process_environment(docker_config=docker_config),
    )


def restart(
    *,
    root: Path,
    service: str,
    runner: CommandRunner | None = None,
    probes: ProbeClient | None = None,
    docker_config: Path | None = None,
) -> dict[str, object]:
    if service not in RESTART_SERVICES:
        raise FbctlError("service is not in the fbctl restart allowlist")
    runner = runner or SubprocessRunner()
    probes = probes or UrllibProbeClient()
    config = load_active(root, docker_config=docker_config)
    environment = sealed_process_environment(docker_config=docker_config)
    plane = "desktop" if service in DESKTOP_SERVICES else "app"
    prefix = ("--profile", "workers") if service in WORKERS else ()
    restart_services = (
        ("vision-webtop", "browser-agent") if service == "vision-webtop" else (service,)
    )
    runner.run(
        config.compose(
            plane,
            *prefix,
            "up",
            "-d",
            "--no-deps",
            "--force-recreate",
            "--wait",
            "--wait-timeout",
            "180",
            *restart_services,
        ),
        step="restart",
        env=environment,
    )
    controller = ProductionController(runner=runner, probes=probes)
    if service in WORKERS:
        controller._verify_worker_heartbeats(config, (service,))  # noqa: SLF001
    elif service in DESKTOP_SERVICES:
        wait_for(
            "browser readiness after restart",
            lambda: require_exact_browser(probes, _api_origin(config), config.api_key),
            timeout=180,
        )
    elif service == "api":
        wait_for(
            "API readiness after restart",
            lambda: require_ok_status(probes, f"{_api_origin(config)}/readyz"),
            timeout=120,
        )
    elif service == "frontend":
        require_ok_status(probes, f"http://127.0.0.1:{config.values['APP_WEB_PORT']}/")
    elif service == "mini-app":
        require_ok_status(
            probes,
            f"http://127.0.0.1:{config.values['APP_TMA_PORT']}/tma/",
        )
    return {"status": "READY", "service": service}


def cleanup(*, root: Path, max_age_hours: int = 24) -> dict[str, object]:
    root = require_absolute_path(root, label="root")
    if max_age_hours < 1:
        raise FbctlError("cleanup max age must be positive")
    active = root / "runtime"
    active_payload: Path | None = None
    if active.is_symlink():
        try:
            active_payload = active.resolve(strict=True)
        except OSError:
            active_payload = None
    removed: list[str] = []
    cutoff = time.time() - max_age_hours * 3600
    for path in [root / "candidate", *root.glob(".runtime-*")]:
        if path == active_payload or not path.exists():
            continue
        metadata = path.lstat()
        if path.is_symlink() or not path.is_dir() or metadata.st_mtime > cutoff:
            continue
        shutil.rmtree(path)
        removed.append(path.name)
    return {"status": "READY", "removed": sorted(removed)}


def db_status(
    *,
    root: Path,
    runner: CommandRunner | None = None,
    docker_config: Path | None = None,
) -> dict[str, object]:
    runner = runner or SubprocessRunner()
    config = load_active(root, docker_config=docker_config)
    environment = sealed_process_environment(docker_config=docker_config)
    schema = runner.run(
        config.compose("jobs", "run", "--rm", "database_check"),
        step="db_status",
        env=environment,
        capture=True,
        check=False,
    )
    receipt = runner.run(
        config.compose("jobs", "run", "--rm", "adoption_status"),
        step="db_status",
        env=environment,
        capture=True,
        check=False,
    )
    migration: object | None = None
    if schema.returncode == 0:
        try:
            migration = json.loads(schema.stdout)
        except json.JSONDecodeError:
            migration = {"evidence": schema.stdout.strip()}
    if schema.returncode != 0:
        selected_status = "FAILED"
    else:
        selected_status = {0: "READY", 3: "UNADOPTED"}.get(receipt.returncode, "FAILED")
    return {
        "status": selected_status,
        "release_id": config.layout.release_id,
        "migration": migration,
        "receipt": receipt.stdout.strip() or None,
        "migration_exit_code": schema.returncode,
        "receipt_exit_code": receipt.returncode,
    }


def db_check(
    *,
    root: Path,
    runner: CommandRunner | None = None,
    docker_config: Path | None = None,
) -> dict[str, object]:
    result = db_status(root=root, runner=runner, docker_config=docker_config)
    if result["status"] != "READY":
        raise FbctlError("database schema/adoption contract check failed", step="db_check")
    return result


def db_migrate(
    *,
    root: Path,
    runner: CommandRunner | None = None,
    docker_config: Path | None = None,
) -> dict[str, object]:
    runner = runner or SubprocessRunner()
    config = load_active(root, docker_config=docker_config)
    controller = ProductionController(runner=runner)
    with controller._deployment_lock(config.layout.root):  # noqa: SLF001
        controller._migrate(config)  # noqa: SLF001
        controller._require_adoption(config)  # noqa: SLF001
    return {"status": "READY", "release_id": config.layout.release_id}


def db_new(
    *,
    source_root: Path,
    message: str,
    runner: CommandRunner | None = None,
) -> dict[str, object]:
    source_root = source_root.resolve(strict=True)
    if source_root != Path.cwd().resolve() or not (source_root / "alembic.ini").is_file():
        raise FbctlError("db new must run from the checked-out source root")
    normalized = message.strip()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 _.-]{2,79}", normalized):
        raise FbctlError("migration message must be 3-80 safe characters")
    executable = source_root / ".venv" / "bin" / "alembic"
    command = os.fspath(executable) if executable.is_file() else "alembic"
    (runner or SubprocessRunner()).run(
        (
            command,
            "-c",
            source_root / "alembic.ini",
            "revision",
            "--autogenerate",
            "-m",
            normalized,
        ),
        step="db_new",
        env=sealed_process_environment(),
    )
    return {"status": "CREATED", "message": normalized}


def _api_origin(config: RuntimeConfig) -> str:
    return f"http://127.0.0.1:{config.values['APP_API_PORT']}"


def _parse_compose_ps(raw: str) -> object:
    raw = raw.strip()
    if not raw:
        return []
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        rows: list[object] = []
        for line in raw.splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                return []
        return rows


def _validate_runtime_ports(config: RuntimeConfig) -> None:
    keys = (
        "APP_API_PORT",
        "APP_WEB_PORT",
        "APP_TMA_PORT",
        "POSTGRES_HOST_PORT",
        "REDIS_HOST_PORT",
        "DESKTOP_HTTPS_PORT",
        "BROWSER_GRPC_HOST_PORT",
    )
    ports: list[int] = []
    for key in keys:
        try:
            port = int(config.values[key])
        except (KeyError, ValueError) as exc:
            raise FbctlError(f"runtime has an invalid port: {key}") from exc
        if port < 1024 or port > 65535:
            raise FbctlError(f"runtime has an unsafe port: {key}")
        ports.append(port)
    if len(set(ports)) != len(ports):
        raise FbctlError("runtime ports are not unique")
