from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import ipaddress
import json
import os
import re
import socket
import stat
import subprocess
import time
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from core.adoption.bundle import (
    AdoptionRecipientV1,
    AdoptionSectionsV1,
    build_adoption_bundle,
    canonical_bundle_json,
)
from fbctl import __main__ as fbctl_main
from fbctl import bundle as fbctl_bundle
from fbctl import controller as fbctl_controller
from fbctl import operations as fbctl_operations
from fbctl import probes as fbctl_probes
from fbctl.adoption import MAX_ADOPTION_BUNDLE_BYTES, verify_adoption_bundle_owner
from fbctl.bundle import (
    BUNDLE_SCHEMA,
    IMAGE_KEYS,
    PREFLIGHT_BUNDLE_SCHEMA,
    RESOURCE_FILES,
    build_bundle,
    inspect_bundle,
)
from fbctl.config import (
    BOOTSTRAP_LEGACY_DROP_KEYS,
    MANAGED_HOST_PORTS,
    SOURCE_ALLOWED_KEYS,
    canonicalize_source,
    load_active,
    prepare_candidate,
    project_bootstrap_source,
    validate_bootstrap_source_check,
    validate_source_values,
)
from fbctl.controller import (
    LEGACY_DOCKER_RESOURCES,
    MANAGED_HOST_PORT_SERVICES,
    REHEARSAL_FAILPOINTS,
    RETIRED_SYSTEMD_UNITS,
    WORKERS,
    DeployOptions,
    ProductionController,
    _normalize_profile_tree,
    _retire_legacy_systemd_units,
    _tcp_port_is_occupied,
    bootstrap_host,
)
from fbctl.errors import FbctlError
from fbctl.files import parse_dotenv
from fbctl.operations import doctor, restart
from fbctl.probes import (
    parse_worker_db_poll_success,
    parse_worker_heartbeat,
    require_system_ready,
)
from fbctl.publish import publish
from fbctl.runner import CommandResult
from fbctl.vision_profile import (
    VISION_PROFILE_MARKER,
    VISION_PROFILE_MARKER_CONTENT,
    snapshot_profile_tree,
)

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "registry.example/fb-agent@sha256:" + "a" * 64
RUNTIME_DERIVED_LEGACY_KEYS = (
    "DEV_TOOLS_ENABLED",
    "FRONTEND_ORIGIN",
    "LOG_FORMAT",
    "SENTRY_ENVIRONMENT",
    "TRACKER_AUTO_CANCEL_ENABLED",
    "TRUST_PROXY_HEADERS",
    "WEB_APP_URL",
)


@pytest.fixture(autouse=True)
def _local_vision_runtime_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unit fixtures use the invoking uid/gid; production constants remain 1000:1000."""

    monkeypatch.setattr(fbctl_controller, "VISION_RUNTIME_UID", os.getuid())
    monkeypatch.setattr(fbctl_controller, "VISION_RUNTIME_GID", os.getgid())
    monkeypatch.setattr(
        fbctl_controller,
        "_tcp_port_is_occupied",
        lambda _host, _port: False,
        raising=False,
    )


def _write(path: Path, content: str | bytes, mode: int = 0o600) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(content, bytes):
        path.write_bytes(content)
    else:
        path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


def _release(release_id: str = "release-1") -> dict[str, object]:
    return {
        "schema": "fb-agent-release/v1",
        "release_id": release_id,
        "images": {key: IMAGE for key in IMAGE_KEYS},
    }


def _manifest(tmp_path: Path, release_id: str = "release-1") -> Path:
    return _write(
        tmp_path / "release.json",
        json.dumps(_release(release_id), sort_keys=True) + "\n",
        0o400,
    )


def _minimal_source() -> dict[str, str]:
    """Минимальный набор ключей, которого хватает canonicalize_source.

    Тот же набор, что _source_env кладёт на диск — вынесен отдельно для
    тестов, которым достаточно словаря значений без временного файла.
    """
    return {
        "ENCRYPTION_KEY": base64.urlsafe_b64encode(b"e" * 32).decode("ascii"),
        "ENCRYPTION_KEY_VERIFY": "verification-value",
        "TELEGRAM_BOT_TOKEN": "123456:test-token",
        "TELEGRAM_OIDC_CLIENT_ID": "123456",
        "TELEGRAM_OIDC_CLIENT_SECRET": "o" * 40,
        "TELEGRAM_OIDC_REDIRECT_URI": "https://app.adpulse.su/auth/telegram/callback",
        "API_KEY": "k" * 32,
        "DESKTOP_OWNER_TELEGRAM_USER_ID": "123456",
    }


def _source_env(tmp_path: Path) -> Path:
    values = _minimal_source()
    return _write(
        tmp_path / "source.env",
        "".join(f"{key}={value}\n" for key, value in values.items()),
    )


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "fb-agent"
    _write_managed_vision_profile(root)
    (root / "shared").chmod(0o700)
    _write(root / "shared" / "source.env", _source_env(tmp_path).read_bytes())
    return root


def _write_managed_vision_profile(root: Path) -> Path:
    profile = root / "shared" / "vision-config"
    profile.mkdir(parents=True, mode=0o700)
    profile.chmod(0o700)
    _write(profile / VISION_PROFILE_MARKER, VISION_PROFILE_MARKER_CONTENT)
    return profile


def _materialize(destination: Path) -> dict[str, object]:
    destination.mkdir(parents=True, mode=0o700)
    release = _release()
    entries: list[dict[str, object]] = []
    payloads: dict[str, bytes] = {
        relative: (ROOT / source).read_bytes() for relative, source in RESOURCE_FILES.items()
    }
    payloads["release.json"] = json.dumps(release, indent=2, sort_keys=True).encode() + b"\n"
    for relative, payload in payloads.items():
        _write(destination / relative, payload, 0o400 if relative.endswith(".json") else 0o644)
        entries.append(
            {
                "path": f"fbctl/resources/{relative}",
                "mode": 0o400 if relative.endswith(".json") else 0o644,
                "size": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        )
    _write(
        destination / "artifact-manifest.json",
        json.dumps(
            {"schema": BUNDLE_SCHEMA, "release_id": release["release_id"], "entries": entries},
            sort_keys=True,
        ),
        0o400,
    )
    return release


class FakeRunner:
    def __init__(
        self,
        *,
        adoption_status: int = 0,
        now: float | None = None,
        fail_remote_deploy: bool = False,
    ) -> None:
        self.commands: list[tuple[str, tuple[str, ...]]] = []
        self.stdin_payloads: list[tuple[str, tuple[str, ...], str | None]] = []
        self.adoption_status = adoption_status
        self.now = now or time.time()
        self.cluster_id = ""
        self.fail_remote_deploy = fail_remote_deploy
        self.uploaded_bundle: Path | None = None

    def run(
        self,
        command,
        *,
        step,
        env=None,
        capture=False,
        check=True,
        input_text=None,
        timeout=None,
    ) -> CommandResult:
        del env, capture, timeout
        argv = tuple(os.fspath(part) for part in command)
        self.commands.append((step, argv))
        self.stdin_payloads.append((step, argv, input_text))
        if argv and argv[0] == "scp":
            self.uploaded_bundle = Path(argv[1])
        if "--env-file" in argv:
            runtime_env = Path(argv[argv.index("--env-file") + 1])
            if runtime_env.is_file():
                for line in runtime_env.read_text(encoding="utf-8").splitlines():
                    if line.startswith("FB_AGENT_BOOTSTRAP_CLUSTER_ID="):
                        self.cluster_id = line.split("=", 1)[1]
        if (
            self.fail_remote_deploy
            and argv[:2] == ("ssh", "deploy@example.test")
            and "sudo" in argv
            and "deploy" in argv
        ):
            result = CommandResult(1)
        elif "adoption_status" in argv:
            result = CommandResult(self.adoption_status)
        elif "mktemp" in argv:
            result = CommandResult(0, f"{argv[-1].replace('XXXXXXXX', 'AbCd1234')}\n")
        elif "sha256sum" in argv:
            assert self.uploaded_bundle is not None
            digest = hashlib.sha256(self.uploaded_bundle.read_bytes()).hexdigest()
            result = CommandResult(0, f"{digest}  {argv[-1]}\n")
        elif "exec" in argv and "http://127.0.0.1:9464/metrics" in " ".join(argv):
            service = argv[argv.index("-T") + 1]
            worker = WORKERS[service]
            metrics = (
                f'fb_agent_worker_heartbeat_timestamp_seconds{{worker="{worker}"}} {self.now}\n'
                f'fb_agent_worker_db_poll_success_timestamp_seconds{{worker="{worker}"}} '
                f"{self.now}\n"
            )
            result = CommandResult(0, metrics)
        elif argv[:2] == ("docker", "network") or argv[:2] == ("docker", "volume"):
            if "inspect" in argv:
                purpose = {
                    "fb_agent_platform": "platform",
                    "fb_agent_infra_pgdata": "infra",
                    "fb_agent_infra_redisdata": "infra",
                    "fb_agent_app_campaign_uploads": "app",
                }.get(argv[-1])
                if purpose is None:
                    result = CommandResult(1)
                else:
                    result = CommandResult(
                        0,
                        json.dumps(
                            {
                                "com.fb-agent.managed": "true",
                                "com.fb-agent.cluster-id": self.cluster_id,
                                "com.fb-agent.purpose": purpose,
                            }
                        ),
                    )
            else:
                result = CommandResult(0)
        elif argv and argv[0] == "ssh" and argv[-3:-1] == ("rm", "-f"):
            result = CommandResult(0)
        else:
            result = CommandResult(0, "[]" if "--format" in argv else "")
        if check and result.returncode:
            raise FbctlError("fake command failed", step=step)
        return result


def test_legacy_systemd_retirement_is_exact_and_preserves_backup_units(
    tmp_path: Path,
) -> None:
    unit_dir = tmp_path / "systemd"
    backup_units = ("fb-agent-backup.service", "fb-agent-backup.timer")
    for unit in (*RETIRED_SYSTEMD_UNITS, *backup_units):
        _write(unit_dir / unit, "legacy\n", 0o644)
    runner = FakeRunner()

    retired = _retire_legacy_systemd_units(runner, unit_dir=unit_dir)

    assert retired == list(RETIRED_SYSTEMD_UNITS)
    assert runner.commands == [
        (
            "bootstrap",
            ("systemctl", "disable", "--now", "fb-agent-healthcheck.timer"),
        ),
        (
            "bootstrap",
            ("systemctl", "disable", "--now", "vision-token-refresh.timer"),
        ),
        ("bootstrap", ("systemctl", "disable", "--now", "fb-agent.service")),
        (
            "bootstrap",
            ("systemctl", "disable", "--now", "fb-agent-release-reconcile.service"),
        ),
        ("bootstrap", ("systemctl", "stop", "fb-agent-healthcheck.service")),
        ("bootstrap", ("systemctl", "stop", "vision-token-refresh.service")),
        ("bootstrap", ("systemctl", "daemon-reload")),
    ]
    assert not any((unit_dir / unit).exists() for unit in RETIRED_SYSTEMD_UNITS)
    assert all((unit_dir / unit).is_file() for unit in backup_units)
    assert not (unit_dir / fbctl_controller.RETIRED_SYSTEMD_RELOAD_MARKER).exists()


def test_legacy_systemd_retirement_is_a_clean_host_noop(tmp_path: Path) -> None:
    runner = FakeRunner()

    assert _retire_legacy_systemd_units(runner, unit_dir=tmp_path / "systemd") == []
    assert runner.commands == []


def test_legacy_systemd_retirement_keeps_files_when_systemctl_fails(tmp_path: Path) -> None:
    unit_dir = tmp_path / "systemd"
    for unit in RETIRED_SYSTEMD_UNITS:
        _write(unit_dir / unit, "legacy\n", 0o644)

    class FailingRunner:
        def run(self, _command, **_kwargs):
            raise FbctlError("systemctl failed", step="bootstrap")

    with pytest.raises(FbctlError, match="systemctl failed"):
        _retire_legacy_systemd_units(FailingRunner(), unit_dir=unit_dir)

    assert all((unit_dir / unit).is_file() for unit in RETIRED_SYSTEMD_UNITS)


def test_legacy_systemd_retirement_retries_reload_after_files_were_removed(
    tmp_path: Path,
) -> None:
    unit_dir = tmp_path / "systemd"
    for unit in RETIRED_SYSTEMD_UNITS:
        _write(unit_dir / unit, "legacy\n", 0o644)

    class ReloadFailingRunner(FakeRunner):
        def run(self, command, **kwargs):
            argv = tuple(os.fspath(part) for part in command)
            if argv == ("systemctl", "daemon-reload"):
                self.commands.append((kwargs["step"], argv))
                raise FbctlError("daemon-reload failed", step=kwargs["step"])
            return super().run(command, **kwargs)

    marker = unit_dir / fbctl_controller.RETIRED_SYSTEMD_RELOAD_MARKER
    with pytest.raises(FbctlError, match="daemon-reload failed"):
        _retire_legacy_systemd_units(ReloadFailingRunner(), unit_dir=unit_dir)

    assert not any((unit_dir / unit).exists() for unit in RETIRED_SYSTEMD_UNITS)
    assert marker.is_file()

    retry_runner = FakeRunner()
    assert _retire_legacy_systemd_units(retry_runner, unit_dir=unit_dir) == []
    assert retry_runner.commands == [("bootstrap", ("systemctl", "daemon-reload"))]
    assert not marker.exists()


def test_legacy_systemd_retirement_rejects_unsafe_exact_path(tmp_path: Path) -> None:
    unit_dir = tmp_path / "systemd"
    (unit_dir / RETIRED_SYSTEMD_UNITS[0]).mkdir(parents=True)
    runner = FakeRunner()

    with pytest.raises(FbctlError, match="legacy systemd unit path is unsafe"):
        _retire_legacy_systemd_units(runner, unit_dir=unit_dir)

    assert runner.commands == []


class DockerInventoryRunner(FakeRunner):
    def __init__(self, resources: dict[tuple[str, str], dict[str, str]]) -> None:
        super().__init__()
        self.resources = dict(resources)

    def run(
        self,
        command,
        *,
        step,
        env=None,
        capture=False,
        check=True,
        input_text=None,
        timeout=None,
    ) -> CommandResult:
        argv = tuple(os.fspath(part) for part in command)
        if argv[:2] in (("docker", "network"), ("docker", "volume")):
            kind = argv[1]
            name = argv[-1]
            if "inspect" in argv:
                self.commands.append((step, argv))
                labels = self.resources.get((kind, name))
                result = (
                    CommandResult(1) if labels is None else CommandResult(0, json.dumps(labels))
                )
                if check and result.returncode:
                    raise FbctlError("fake command failed", step=step)
                return result
            if "create" in argv:
                self.commands.append((step, argv))
                labels = {
                    argv[index + 1].split("=", 1)[0]: argv[index + 1].split("=", 1)[1]
                    for index, value in enumerate(argv)
                    if value == "--label"
                }
                self.resources[(kind, name)] = labels
                return CommandResult(0)
        return super().run(
            command,
            step=step,
            env=env,
            capture=capture,
            check=check,
            input_text=input_text,
            timeout=timeout,
        )


class DockerPortInventoryRunner(DockerInventoryRunner):
    def __init__(self, containers: list[dict[str, object]]) -> None:
        super().__init__({})
        self.containers = containers

    def run(
        self,
        command,
        *,
        step,
        env=None,
        capture=False,
        check=True,
        input_text=None,
        timeout=None,
    ) -> CommandResult:
        argv = tuple(os.fspath(part) for part in command)
        if argv in {
            ("docker", "container", "ls", "--quiet", "--no-trunc"),
            ("docker", "container", "ls", "--all", "--quiet", "--no-trunc"),
        }:
            self.commands.append((step, argv))
            return CommandResult(
                0,
                "".join(f"{container['Id']}\n" for container in self.containers),
            )
        if argv[:3] == ("docker", "container", "inspect"):
            self.commands.append((step, argv))
            return CommandResult(0, json.dumps(self.containers))
        return super().run(
            command,
            step=step,
            env=env,
            capture=capture,
            check=check,
            input_text=input_text,
            timeout=timeout,
        )


class RecordingTcpPortProbe:
    def __init__(self, *occupied_ports: int) -> None:
        self.occupied_ports = frozenset(occupied_ports)
        self.calls: list[tuple[str, int]] = []

    def __call__(self, host: str, port: int) -> bool:
        self.calls.append((host, port))
        return port in self.occupied_ports


class FakeProbes:
    def status(self, url: str, *, timeout: float = 15) -> int:
        del timeout
        if url == "https://desktop.adpulse.su/":
            return 303
        return 200

    def json(self, url: str, *, headers=None, timeout: float = 15):
        del headers, timeout
        if url.endswith("/openapi.json"):
            return 200, {
                "paths": {
                    "/api/operator/snapshot": {},
                    "/api/v1/integrations/telegram/webhook": {},
                    "/api/settings/vision": {},
                    "/api/settings/observer/scanning": {},
                }
            }
        if url.endswith("/api/operator/snapshot"):
            section = {"state": "empty", "sources": [], "issues": []}
            return 200, {
                "meta": {"revision": "r1", "generated_at": "2026-01-01T00:00:00Z"},
                **{
                    name: dict(section)
                    for name in (
                        "attention",
                        "portfolio",
                        "economy",
                        "funnel",
                        "actions",
                        "system",
                    )
                },
            }
        if url.endswith("/api/settings/vision"):
            return 200, {
                "required_browser_contract_version": 5,
                "browser_contract_version": 5,
                "browser_contract_compatible": True,
                "profile_id": "profile-1",
                "live_profile_id": "profile-1",
                "graph_probe_performed": True,
                "graph_probe_ok": True,
                "channel_status": "READY",
                "browser_session_id": "session-1",
            }
        if url.endswith("/system-readyz"):
            return 200, {
                "ready": True,
                "infrastructure_ready": True,
                "overall": "HEALTHY",
                "blockers": [],
                "degraded": [],
            }
        if url.endswith("/api/settings/telegram/diagnostics"):
            return 200, {
                "webhook_state": "configured",
                "webhook_remote_url_matches": True,
            }
        raise AssertionError(url)

    def patch_json(self, url: str, payload, *, headers=None, timeout: float = 15):
        del headers, timeout
        assert url.endswith("/api/settings/observer/scanning")
        assert payload == {"enabled": True}
        return 200, {"is_scanning_enabled": True}

    def post_json(self, url: str, payload, *, headers=None, timeout: float = 15):
        del headers, timeout
        assert url.endswith("/api/vision/ensure-cdp")
        assert payload == {}
        return 200, {"ok": True, "status": "READY", "action": "none", "message": ""}


def test_bootstrap_preflight_rejects_foreign_new_resource_before_creation(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    config = prepare_candidate(
        root=root,
        release=_materialize(root / "candidate"),
        source_env=None,
        docker_config=None,
        adoption_bundle=None,
        rehearsal=True,
    )
    resource_name = "fb_agent_infra_redisdata"
    runner = DockerInventoryRunner({("volume", resource_name): {}})
    controller = ProductionController(runner=runner)

    with pytest.raises(
        FbctlError,
        match=(
            rf"Docker volume is not managed by fbctl: {resource_name}; "
            "inspect and resolve this name collision manually before retrying"
        ),
    ):
        controller._preflight(  # noqa: SLF001 - exact bootstrap preflight contract
            config,
            DeployOptions(root=root, rehearsal=True),
            require_resources=False,
            validate_caddy=False,
        )

    assert not any("create" in command for _step, command in runner.commands)


def test_preflight_reports_legacy_resources_without_mutating_or_failing(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    config = prepare_candidate(
        root=root,
        release=_materialize(root / "candidate"),
        source_env=None,
        docker_config=None,
        adoption_bundle=None,
        rehearsal=True,
    )
    legacy_kind, legacy_name = LEGACY_DOCKER_RESOURCES[1]
    runner = DockerInventoryRunner({(legacy_kind, legacy_name): {}})
    log: list[str] = []
    controller = ProductionController(runner=runner, log=log.append)

    controller._preflight(  # noqa: SLF001 - exact bootstrap preflight contract
        config,
        DeployOptions(root=root, rehearsal=True),
        require_resources=False,
        validate_caddy=False,
    )

    assert log == [
        "[fbctl] info: legacy Docker resources detected and left untouched: "
        f"{legacy_kind} {legacy_name}"
    ]
    legacy_commands = [command for _step, command in runner.commands if command[-1] == legacy_name]
    assert legacy_commands == [("docker", legacy_kind, "inspect", legacy_name)]


def test_preflight_rejects_occupied_infra_ports_before_mutation(tmp_path: Path) -> None:
    root = _root(tmp_path)
    config = prepare_candidate(
        root=root,
        release=_materialize(root / "candidate"),
        source_env=None,
        docker_config=None,
        adoption_bundle=None,
        rehearsal=True,
    )
    postgres_id = "a" * 64
    redis_id = "b" * 64
    runner = DockerPortInventoryRunner(
        [
            {
                "Id": postgres_id,
                "Name": "/legacy-postgres",
                "Config": {"Labels": {}},
                "NetworkSettings": {
                    "Ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5433"}]}
                },
            },
            {
                "Id": redis_id,
                "Name": "/legacy-redis",
                "Config": {"Labels": {}},
                "NetworkSettings": {
                    "Ports": {"6379/tcp": [{"HostIp": "0.0.0.0", "HostPort": "6380"}]}
                },
            },
        ]
    )
    controller = ProductionController(
        runner=runner,
        port_probe=RecordingTcpPortProbe(5433, 6380),
    )

    with pytest.raises(FbctlError) as caught:
        controller._preflight(  # noqa: SLF001 - exact bootstrap preflight contract
            config,
            DeployOptions(root=root, rehearsal=True),
            require_resources=False,
            validate_caddy=False,
        )

    message = str(caught.value)
    assert "POSTGRES_HOST_PORT=5433 is occupied by container legacy-postgres" in message
    assert "sudo docker stop legacy-postgres" in message
    assert "REDIS_HOST_PORT=6380 is occupied by container legacy-redis" in message
    assert "sudo docker stop legacy-redis" in message
    mutation_words = {"create", "down", "rm", "rename", "stop", "up"}
    assert not any(mutation_words.intersection(command) for _step, command in runner.commands)


def test_preflight_allows_current_managed_infra_port_owner(tmp_path: Path) -> None:
    root = _root(tmp_path)
    config = prepare_candidate(
        root=root,
        release=_materialize(root / "candidate"),
        source_env=None,
        docker_config=None,
        adoption_bundle=None,
        rehearsal=True,
    )
    labels = {
        "com.fb-agent.managed": "true",
        "com.fb-agent.cluster-id": config.values["FB_AGENT_BOOTSTRAP_CLUSTER_ID"],
        "com.fb-agent.purpose": "infra",
        "com.docker.compose.project": config.values["INFRA_PROJECT_NAME"],
        "com.docker.compose.service": "postgres",
    }
    runner = DockerPortInventoryRunner(
        [
            {
                "Id": "c" * 64,
                "Name": "/fb_agent_infra-postgres-1",
                "Config": {"Labels": labels},
                "NetworkSettings": {
                    "Ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5433"}]}
                },
            }
        ]
    )
    controller = ProductionController(
        runner=runner,
        port_probe=RecordingTcpPortProbe(5433),
    )

    controller._preflight(  # noqa: SLF001 - exact deploy preflight contract
        config,
        DeployOptions(root=root, rehearsal=True),
        require_resources=False,
        validate_caddy=False,
    )


def test_preflight_rejects_occupied_app_and_desktop_ports(tmp_path: Path) -> None:
    """Коллизия вне infra обязана всплыть в preflight, а не после stop_runtime."""

    root = _root(tmp_path)
    config = prepare_candidate(
        root=root,
        release=_materialize(root / "candidate"),
        source_env=None,
        docker_config=None,
        adoption_bundle=None,
        rehearsal=True,
    )
    runner = DockerPortInventoryRunner(
        [
            {
                "Id": "d" * 64,
                "Name": "/stray-api",
                "Config": {"Labels": {}},
                "NetworkSettings": {
                    "Ports": {
                        "8100/tcp": [
                            {
                                "HostIp": "127.0.0.1",
                                "HostPort": config.values["APP_API_PORT"],
                            }
                        ]
                    }
                },
            },
            {
                "Id": "e" * 64,
                "Name": "/stray-desktop",
                "Config": {"Labels": {}},
                "NetworkSettings": {
                    "Ports": {
                        "50051/tcp": [
                            {
                                "HostIp": "127.0.0.1",
                                "HostPort": config.values["BROWSER_GRPC_HOST_PORT"],
                            }
                        ]
                    }
                },
            },
        ]
    )
    controller = ProductionController(
        runner=runner,
        port_probe=RecordingTcpPortProbe(
            int(config.values["APP_API_PORT"]),
            int(config.values["BROWSER_GRPC_HOST_PORT"]),
        ),
    )

    with pytest.raises(FbctlError) as caught:
        controller._preflight(  # noqa: SLF001 - exact deploy preflight contract
            config,
            DeployOptions(root=root, rehearsal=True),
            require_resources=False,
            validate_caddy=False,
        )

    message = str(caught.value)
    assert f"APP_API_PORT={config.values['APP_API_PORT']} is occupied" in message
    assert (
        f"BROWSER_GRPC_HOST_PORT={config.values['BROWSER_GRPC_HOST_PORT']} is occupied" in message
    )
    assert "sudo docker stop stray-api" in message
    assert "sudo docker stop stray-desktop" in message


def test_preflight_allows_current_app_project_port_owner(tmp_path: Path) -> None:
    """Свой же app-контур не коллизия: его остановит сам deploy."""

    root = _root(tmp_path)
    config = prepare_candidate(
        root=root,
        release=_materialize(root / "candidate"),
        source_env=None,
        docker_config=None,
        adoption_bundle=None,
        rehearsal=True,
    )
    runner = DockerPortInventoryRunner(
        [
            {
                "Id": "f" * 64,
                "Name": "/fb_agent_app-api-1",
                "Config": {
                    "Labels": {
                        "com.fb-agent.managed": "true",
                        "com.fb-agent.cluster-id": config.values["FB_AGENT_BOOTSTRAP_CLUSTER_ID"],
                        "com.docker.compose.project": config.values["APP_PROJECT_NAME"],
                        "com.docker.compose.service": "api",
                    }
                },
                "NetworkSettings": {
                    "Ports": {
                        "8100/tcp": [
                            {
                                "HostIp": "127.0.0.1",
                                "HostPort": config.values["APP_API_PORT"],
                            }
                        ]
                    }
                },
            }
        ]
    )
    controller = ProductionController(
        runner=runner,
        port_probe=RecordingTcpPortProbe(int(config.values["APP_API_PORT"])),
    )

    controller._preflight(  # noqa: SLF001 - exact deploy preflight contract
        config,
        DeployOptions(root=root, rehearsal=True),
        require_resources=False,
        validate_caddy=False,
    )


def test_preflight_rejects_host_process_without_docker_owner(tmp_path: Path) -> None:
    root = _root(tmp_path)
    config = prepare_candidate(
        root=root,
        release=_materialize(root / "candidate"),
        source_env=None,
        docker_config=None,
        adoption_bundle=None,
        rehearsal=True,
    )
    probe = RecordingTcpPortProbe(int(config.values["POSTGRES_HOST_PORT"]))
    controller = ProductionController(
        runner=DockerPortInventoryRunner([]),
        sleep=lambda _seconds: None,
    )
    controller.port_probe = probe

    with pytest.raises(FbctlError) as caught:
        controller._preflight(  # noqa: SLF001 - exact deploy preflight contract
            config,
            DeployOptions(root=root, rehearsal=True),
            require_resources=False,
            validate_caddy=False,
        )

    message = str(caught.value)
    assert "POSTGRES_HOST_PORT=5433 is occupied" in message
    assert "process outside Docker" in message
    assert "free the port manually before retrying" in message
    # Каждый управляемый порт опрошен, а спорный — переспрошен: догорающий
    # сокет освобождается за секунды, чужой процесс держит порт устойчиво.
    assert set(probe.calls) >= {
        ("127.0.0.1", int(config.values[key])) for key, _default in MANAGED_HOST_PORTS
    }
    postgres_probe = ("127.0.0.1", int(config.values["POSTGRES_HOST_PORT"]))
    assert probe.calls.count(postgres_probe) > 1
    assert set(probe.calls) == {
        ("127.0.0.1", int(config.values[key])) for key, _default in MANAGED_HOST_PORTS
    }


def test_preflight_probes_but_allows_current_managed_tcp_owner(tmp_path: Path) -> None:
    root = _root(tmp_path)
    config = prepare_candidate(
        root=root,
        release=_materialize(root / "candidate"),
        source_env=None,
        docker_config=None,
        adoption_bundle=None,
        rehearsal=True,
    )
    postgres_port = int(config.values["POSTGRES_HOST_PORT"])
    probe = RecordingTcpPortProbe(postgres_port)
    runner = DockerPortInventoryRunner(
        [
            {
                "Id": "2" * 64,
                "Name": "/fb_agent_infra-postgres-1",
                "Config": {
                    "Labels": {
                        "com.fb-agent.managed": "true",
                        "com.fb-agent.cluster-id": config.values["FB_AGENT_BOOTSTRAP_CLUSTER_ID"],
                        "com.docker.compose.project": config.values["INFRA_PROJECT_NAME"],
                        "com.docker.compose.service": "postgres",
                    }
                },
                "NetworkSettings": {
                    "Ports": {
                        "5432/tcp": [
                            {
                                "HostIp": "127.0.0.1",
                                "HostPort": str(postgres_port),
                            }
                        ]
                    }
                },
            }
        ]
    )
    controller = ProductionController(runner=runner, port_probe=probe)

    controller._preflight(  # noqa: SLF001 - exact deploy preflight contract
        config,
        DeployOptions(root=root, rehearsal=True),
        require_resources=False,
        validate_caddy=False,
    )

    assert ("127.0.0.1", postgres_port) in probe.calls


def test_preflight_ignores_udp_mapping_for_occupied_tcp_port(tmp_path: Path) -> None:
    root = _root(tmp_path)
    config = prepare_candidate(
        root=root,
        release=_materialize(root / "candidate"),
        source_env=None,
        docker_config=None,
        adoption_bundle=None,
        rehearsal=True,
    )
    postgres_port = int(config.values["POSTGRES_HOST_PORT"])
    runner = DockerPortInventoryRunner(
        [
            {
                "Id": "3" * 64,
                "Name": "/foreign-udp",
                "Config": {"Labels": {}},
                "NetworkSettings": {
                    "Ports": {"5432/udp": [{"HostIp": "127.0.0.1", "HostPort": str(postgres_port)}]}
                },
            },
            {
                "Id": "4" * 64,
                "Name": "/fb_agent_infra-postgres-1",
                "Config": {
                    "Labels": {
                        "com.fb-agent.managed": "true",
                        "com.fb-agent.cluster-id": config.values["FB_AGENT_BOOTSTRAP_CLUSTER_ID"],
                        "com.docker.compose.project": config.values["INFRA_PROJECT_NAME"],
                        "com.docker.compose.service": "postgres",
                    }
                },
                "NetworkSettings": {
                    "Ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(postgres_port)}]}
                },
            },
        ]
    )
    controller = ProductionController(runner=runner)
    controller.port_probe = RecordingTcpPortProbe(postgres_port)

    controller._preflight(  # noqa: SLF001 - exact deploy preflight contract
        config,
        DeployOptions(root=root, rehearsal=True),
        require_resources=False,
        validate_caddy=False,
    )


def test_preflight_rejects_stopped_restart_policy_port_owner(tmp_path: Path) -> None:
    root = _root(tmp_path)
    config = prepare_candidate(
        root=root,
        release=_materialize(root / "candidate"),
        source_env=None,
        docker_config=None,
        adoption_bundle=None,
        rehearsal=True,
    )
    app_port = int(config.values["APP_API_PORT"])
    runner = DockerPortInventoryRunner(
        [
            {
                "Id": "5" * 64,
                "Name": "/stopped-api",
                "Config": {"Labels": {}},
                "State": {"Status": "exited"},
                "HostConfig": {
                    "RestartPolicy": {"Name": "always"},
                    "PortBindings": {
                        "8100/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(app_port)}]
                    },
                },
                "NetworkSettings": {"Ports": {}},
            }
        ]
    )
    controller = ProductionController(runner=runner)
    controller.port_probe = RecordingTcpPortProbe(app_port)

    with pytest.raises(FbctlError) as caught:
        controller._preflight(  # noqa: SLF001 - exact deploy preflight contract
            config,
            DeployOptions(root=root, rehearsal=True),
            require_resources=False,
            validate_caddy=False,
        )

    message = str(caught.value)
    assert f"APP_API_PORT={app_port} is occupied by container stopped-api" in message
    assert "sudo docker stop stopped-api" in message
    assert any(
        command == ("docker", "container", "ls", "--all", "--quiet", "--no-trunc")
        for _step, command in runner.commands
    )


def test_preflight_does_not_treat_ipv6_wildcard_as_ipv4_owner(tmp_path: Path) -> None:
    root = _root(tmp_path)
    config = prepare_candidate(
        root=root,
        release=_materialize(root / "candidate"),
        source_env=None,
        docker_config=None,
        adoption_bundle=None,
        rehearsal=True,
    )
    postgres_port = int(config.values["POSTGRES_HOST_PORT"])
    own_labels = {
        "com.fb-agent.managed": "true",
        "com.fb-agent.cluster-id": config.values["FB_AGENT_BOOTSTRAP_CLUSTER_ID"],
        "com.docker.compose.project": config.values["INFRA_PROJECT_NAME"],
        "com.docker.compose.service": "postgres",
    }
    runner = DockerPortInventoryRunner(
        [
            {
                "Id": "6" * 64,
                "Name": "/foreign-ipv6",
                "Config": {"Labels": {}},
                "NetworkSettings": {
                    "Ports": {"5432/tcp": [{"HostIp": "::", "HostPort": str(postgres_port)}]}
                },
            },
            {
                "Id": "7" * 64,
                "Name": "/fb_agent_infra-postgres-1",
                "Config": {"Labels": own_labels},
                "NetworkSettings": {
                    "Ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": str(postgres_port)}]}
                },
            },
        ]
    )
    controller = ProductionController(runner=runner)
    controller.port_probe = RecordingTcpPortProbe(postgres_port)

    controller._preflight(  # noqa: SLF001 - exact deploy preflight contract
        config,
        DeployOptions(root=root, rehearsal=True),
        require_resources=False,
        validate_caddy=False,
    )


def test_preflight_matches_ipv4_mapped_ipv6_port_owner(tmp_path: Path) -> None:
    root = _root(tmp_path)
    config = prepare_candidate(
        root=root,
        release=_materialize(root / "candidate"),
        source_env=None,
        docker_config=None,
        adoption_bundle=None,
        rehearsal=True,
    )
    postgres_port = int(config.values["POSTGRES_HOST_PORT"])
    runner = DockerPortInventoryRunner(
        [
            {
                "Id": "8" * 64,
                "Name": "/foreign-mapped-ipv6",
                "Config": {"Labels": {}},
                "NetworkSettings": {
                    "Ports": {
                        "5432/tcp": [
                            {
                                "HostIp": "::ffff:127.0.0.1",
                                "HostPort": str(postgres_port),
                            }
                        ]
                    }
                },
            }
        ]
    )
    controller = ProductionController(runner=runner)
    controller.port_probe = RecordingTcpPortProbe(postgres_port)

    with pytest.raises(FbctlError) as caught:
        controller._preflight(  # noqa: SLF001 - exact deploy preflight contract
            config,
            DeployOptions(root=root, rehearsal=True),
            require_resources=False,
            validate_caddy=False,
        )

    message = str(caught.value)
    assert "POSTGRES_HOST_PORT=5433 is occupied by container foreign-mapped-ipv6" in message
    assert "sudo docker stop foreign-mapped-ipv6" in message


def test_preflight_survives_container_disappearing_between_ls_and_inspect(
    tmp_path: Path,
) -> None:
    """Контейнер завершился между ls и inspect: порт он не держит, падать нельзя."""

    root = _root(tmp_path)
    config = prepare_candidate(
        root=root,
        release=_materialize(root / "candidate"),
        source_env=None,
        docker_config=None,
        adoption_bundle=None,
        rehearsal=True,
    )

    class VanishingContainerRunner(DockerPortInventoryRunner):
        def run(self, command, **kwargs):
            argv = tuple(os.fspath(part) for part in command)
            if argv[:3] == ("docker", "container", "inspect"):
                self.commands.append((kwargs["step"], argv))
                # docker печатает JSON по выжившим и выходит с кодом 1.
                return CommandResult(1, "[]")
            return super().run(command, **kwargs)

    runner = VanishingContainerRunner(
        [
            {
                "Id": "1" * 64,
                "Name": "/vanished",
                "Config": {"Labels": {}},
                "NetworkSettings": {
                    "Ports": {"5432/tcp": [{"HostIp": "127.0.0.1", "HostPort": "5433"}]}
                },
            }
        ]
    )
    controller = ProductionController(runner=runner)

    controller._preflight(  # noqa: SLF001 - exact deploy preflight contract
        config,
        DeployOptions(root=root, rehearsal=True),
        require_resources=False,
        validate_caddy=False,
    )


def test_deploy_preflight_requires_existing_managed_resources(tmp_path: Path) -> None:
    """Обычный deploy обязан требовать ресурсы: allow_missing не ослабил его."""

    root = _root(tmp_path)
    config = prepare_candidate(
        root=root,
        release=_materialize(root / "candidate"),
        source_env=None,
        docker_config=None,
        adoption_bundle=None,
        rehearsal=True,
    )
    runner = DockerInventoryRunner({})
    controller = ProductionController(runner=runner)

    with pytest.raises(FbctlError, match="managed Docker network is missing: fb_agent_platform"):
        controller._preflight(  # noqa: SLF001 - exact deploy preflight contract
            config,
            DeployOptions(root=root, rehearsal=True),
            require_resources=True,
            validate_caddy=False,
        )


def test_preflight_rejects_resource_from_another_cluster(tmp_path: Path) -> None:
    """Чужой cluster-id на нашем имени — fail-closed, а не молчаливое принятие."""

    root = _root(tmp_path)
    config = prepare_candidate(
        root=root,
        release=_materialize(root / "candidate"),
        source_env=None,
        docker_config=None,
        adoption_bundle=None,
        rehearsal=True,
    )
    runner = DockerInventoryRunner(
        {
            ("network", config.values["PLATFORM_NETWORK"]): {
                "com.fb-agent.managed": "true",
                "com.fb-agent.cluster-id": "0" * 32,
                "com.fb-agent.purpose": "platform",
            }
        }
    )
    controller = ProductionController(runner=runner)

    with pytest.raises(FbctlError, match="belongs to another cluster"):
        controller._preflight(  # noqa: SLF001 - exact deploy preflight contract
            config,
            DeployOptions(root=root, rehearsal=True),
            require_resources=True,
            validate_caddy=False,
        )


def test_bootstrap_creates_only_the_new_managed_resource_names(tmp_path: Path) -> None:
    root = _root(tmp_path)
    config = prepare_candidate(
        root=root,
        release=_materialize(root / "candidate"),
        source_env=None,
        docker_config=None,
        adoption_bundle=None,
        rehearsal=True,
    )
    runner = DockerInventoryRunner({})
    controller = ProductionController(runner=runner)

    controller._ensure_bootstrap_resources(config)  # noqa: SLF001

    created = [
        command[-1]
        for step, command in runner.commands
        if step == "bootstrap_resources" and "create" in command
    ]
    assert created == [
        "fb_agent_platform",
        "fb_agent_infra_pgdata",
        "fb_agent_infra_redisdata",
        "fb_agent_app_campaign_uploads",
    ]


def test_bootstrap_retry_creates_only_resources_missing_after_interruption(tmp_path: Path) -> None:
    root = _root(tmp_path)
    config = prepare_candidate(
        root=root,
        release=_materialize(root / "candidate"),
        source_env=None,
        docker_config=None,
        adoption_bundle=None,
        rehearsal=True,
    )
    cluster_id = config.values["FB_AGENT_BOOTSTRAP_CLUSTER_ID"]

    def labels(purpose: str) -> dict[str, str]:
        return {
            "com.fb-agent.managed": "true",
            "com.fb-agent.cluster-id": cluster_id,
            "com.fb-agent.purpose": purpose,
        }

    runner = DockerInventoryRunner(
        {
            ("network", "fb_agent_platform"): labels("platform"),
            ("volume", "fb_agent_infra_pgdata"): labels("infra"),
        }
    )
    controller = ProductionController(runner=runner)

    controller._ensure_bootstrap_resources(config)  # noqa: SLF001

    created = [
        command[-1]
        for step, command in runner.commands
        if step == "bootstrap_resources" and "create" in command
    ]
    assert created == [
        "fb_agent_infra_redisdata",
        "fb_agent_app_campaign_uploads",
    ]


def test_interrupted_candidate_materialization_is_replaced_before_retry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    package = tmp_path / "package"
    resources = package / "resources"
    for relative in (*RESOURCE_FILES, "release.json", "artifact-manifest.json"):
        _write(resources / relative, f"fixture:{relative}\n")
    candidate = tmp_path / "candidate"
    _write(candidate / "partial-from-interrupted-run", "stale\n")
    monkeypatch.setattr(fbctl_bundle.importlib.resources, "files", lambda _package: package)
    monkeypatch.setattr(fbctl_bundle, "embedded_release", _release)

    release = fbctl_bundle.materialize_candidate(candidate)

    assert release == _release()
    assert not (candidate / "partial-from-interrupted-run").exists()
    assert all((candidate / relative).is_file() for relative in RESOURCE_FILES)


def test_bundle_is_deterministic_and_runnable(tmp_path: Path) -> None:
    first = tmp_path / "first.pyz"
    second = tmp_path / "second.pyz"
    manifest = _manifest(tmp_path)

    one = build_bundle(
        source_root=ROOT,
        output=first,
        release_id="release-1",
        release_manifest=manifest,
    )
    two = build_bundle(
        source_root=ROOT,
        output=second,
        release_id="release-1",
        release_manifest=manifest,
    )

    assert one["sha256"] == two["sha256"]
    assert first.read_bytes() == second.read_bytes()
    assert inspect_bundle(first).release_id == "release-1"
    result = subprocess.run(
        ["python3", str(first), "--help"],
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    assert "publish" in result.stdout


@pytest.mark.parametrize("command", ["doctor", "status"])
def test_red_operational_status_has_nonzero_cli_exit(command, monkeypatch, capsys) -> None:
    monkeypatch.setattr(
        fbctl_main,
        "_dispatch",
        lambda _args: {"status": "FAILED" if command == "doctor" else "DEGRADED"},
    )
    assert fbctl_main.main([command, "--root", "/tmp/fb-agent-test"]) == 1
    assert '"status"' in capsys.readouterr().out


def test_doctor_cannot_report_ready_without_an_active_runtime(tmp_path: Path) -> None:
    root = _root(tmp_path)

    result = doctor(root=root, runner=FakeRunner())

    assert result["status"] == "FAILED"
    assert any(str(error).startswith("active_runtime_unavailable:") for error in result["errors"])


def test_doctor_rejects_preflight_bundle_as_active_control_bundle(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    release = _materialize(root / "candidate")
    config = prepare_candidate(
        root=root,
        release=release,
        source_env=None,
        docker_config=None,
        adoption_bundle=None,
    )
    monkeypatch.setattr(fbctl_operations, "load_active", lambda *_args, **_kwargs: config)
    monkeypatch.setattr(
        fbctl_operations,
        "inspect_bundle",
        lambda _path: SimpleNamespace(
            schema=PREFLIGHT_BUNDLE_SCHEMA,
            release_id=config.layout.release_id,
        ),
    )

    result = doctor(root=root, runner=FakeRunner())

    assert result["status"] == "FAILED"
    assert "active control bundle is not a full release bundle" in result["errors"]


def test_fbctl_source_tree_remains_bytecode_free() -> None:
    assert not list((ROOT / "fbctl").rglob("*.pyc"))
    assert not list((ROOT / "fbctl").rglob("__pycache__"))


def test_bundle_rejects_unknown_release_manifest_keys(tmp_path: Path) -> None:
    payload = _release() | {"legacy": True}
    manifest = _write(tmp_path / "release.json", json.dumps(payload), 0o400)
    with pytest.raises(FbctlError, match="exactly schema"):
        build_bundle(
            source_root=ROOT,
            output=tmp_path / "release.pyz",
            release_id="release-1",
            release_manifest=manifest,
        )


def test_source_contract_rejects_unknown_keys() -> None:
    with pytest.raises(FbctlError, match="unsupported key LEGACY_EXPORT"):
        canonicalize_source({"LEGACY_EXPORT": "1"}, incumbent={})


def _legacy_source_values() -> dict[str, str]:
    return {
        "API_HOST": "0.0.0.0",
        "API_PORT": "8100",
        "DESKTOP_GUACAMOLE_POSTGRES_DB": "guacamole",
        "DESKTOP_GUACAMOLE_POSTGRES_HOST": "legacy-guacamole-db",
        "DESKTOP_GUACAMOLE_POSTGRES_PASSWORD": "guacamole-secret",
        "DESKTOP_GUACAMOLE_POSTGRES_PORT": "5432",
        "DESKTOP_GUACAMOLE_POSTGRES_USER": "guacamole",
        "DESKTOP_PUBLIC_ORIGIN": "https://legacy.example.invalid",
        "DESKTOP_VNC_PASSWORD": "vnc-secret",
        "DESKTOP_WEBTOP_IMAGE": "legacy.example.invalid/webtop:latest",
        "DEV_TOOLS_ENABLED": "legacy-enable-dev-tools",
        "FRONTEND_ORIGIN": "http://legacy.example.invalid",
        "GRPC_PORT": "50051",
        "LOG_FORMAT": "legacy-plain-text",
        "POSTGRES_HOST": "localhost",
        "POSTGRES_PORT": "5433",
        "REDIS_URL": "redis://super-secret@localhost:6380/0",
        "REQUIRE_API_KEY": "true",
        "SENTRY_ENVIRONMENT": "legacy-staging",
        "TRACKER_AUTO_CANCEL_ENABLED": "legacy-disable-money-cancel",
        "TRUST_PROXY_HEADERS": "legacy-disable-proxy",
        "VISION_API_URL": "http://127.0.0.1:3030",
        "VISION_AUTO_RESTART_ON_MISSING_CDP": "true",
        "VISION_PASSWORD": "vision-secret",
        "VISION_TEAM_ID": "team-secret",
        "VISION_USERNAME": "vision-owner",
        "TELEGRAM_CHAT_ID": "123456",
        "VISION_FOLDER_ID": "folder-current",
        "WEB_APP_URL": "http://legacy.example.invalid/tma/?token=never-persist",
    }


def _adoption_bundle_payload(recipients: list[dict[str, object]]) -> dict[str, object]:
    recipient_bytes = json.dumps(
        recipients,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return {
        "schema_version": "adoption-bundle/v1",
        "entity_counts": {"recipients": len(recipients)},
        "section_sha256": {"recipients": hashlib.sha256(recipient_bytes).hexdigest()},
        "sections": {"recipients": recipients},
    }


def _owner_recipient(telegram_user_id: int = 123456) -> dict[str, object]:
    return {
        "chat_id": telegram_user_id,
        "telegram_user_id": telegram_user_id,
        "username": "owner",
        "display_name": "Owner",
        "role": "owner",
    }


def test_bootstrap_projects_only_the_exact_known_legacy_source_shape() -> None:
    source = {
        **_legacy_source_values(),
        "DESKTOP_OWNER_TELEGRAM_USER_ID": "123456",
    }

    projected, dropped = project_bootstrap_source(source, project_known_legacy_source=True)

    assert dropped == tuple(sorted(set(_legacy_source_values()) - {"VISION_FOLDER_ID"}))
    assert projected == {
        "DESKTOP_OWNER_TELEGRAM_USER_ID": "123456",
        "VISION_FOLDER_ID": "folder-current",
    }


def test_bootstrap_drop_list_never_swallows_a_supported_key() -> None:
    """Отбрасываемый ключ не должен быть одновременно принимаемым.

    Пересечение означало бы, что bootstrap молча выбрасывает значение,
    которое оператор задал осознанно, и разница вскроется только на
    работающем production.
    """
    assert not (BOOTSTRAP_LEGACY_DROP_KEYS & SOURCE_ALLOWED_KEYS)


def test_bootstrap_projection_reports_all_unknown_names_without_values() -> None:
    secret = "never-print-this-secret"
    with pytest.raises(FbctlError) as raised:
        project_bootstrap_source(
            {
                **_legacy_source_values(),
                "DESKTOP_OWNER_TELEGRAM_USER_ID": "123456",
                "UNSAFE_B": secret,
                "UNSAFE_A": secret,
            },
            project_known_legacy_source=True,
        )

    assert str(raised.value) == "source environment contains unsupported keys: UNSAFE_A, UNSAFE_B"
    assert secret not in str(raised.value)


@pytest.mark.parametrize(
    "legacy_key",
    RUNTIME_DERIVED_LEGACY_KEYS,
)
def test_runtime_derived_legacy_keys_remain_strict_without_bootstrap_projection(
    legacy_key: str,
) -> None:
    with pytest.raises(FbctlError, match=rf"unsupported key {legacy_key}"):
        canonicalize_source({legacy_key: "legacy-value"}, incumbent={})


@pytest.mark.parametrize("chat_id", ["0", "999999", "not-a-number"])
def test_bootstrap_projection_drops_obsolete_telegram_chat_id_without_parsing(chat_id: str) -> None:
    projected, dropped = project_bootstrap_source(
        {
            "DESKTOP_OWNER_TELEGRAM_USER_ID": "123456",
            "TELEGRAM_CHAT_ID": chat_id,
        },
        project_known_legacy_source=True,
    )

    assert projected == {"DESKTOP_OWNER_TELEGRAM_USER_ID": "123456"}
    assert dropped == ("TELEGRAM_CHAT_ID",)


def test_routine_canonicalization_keeps_telegram_chat_id_strict() -> None:
    with pytest.raises(FbctlError, match="unsupported key TELEGRAM_CHAT_ID"):
        canonicalize_source(
            {
                "DESKTOP_OWNER_TELEGRAM_USER_ID": "123456",
                "TELEGRAM_CHAT_ID": "123456",
            },
            incumbent={},
        )


def test_adoption_owner_preflight_returns_exact_matching_owner_bytes(tmp_path: Path) -> None:
    payload = json.dumps(_adoption_bundle_payload([_owner_recipient()])).encode() + b"\n"
    bundle = _write(
        tmp_path / "adoption.json",
        payload,
    )

    assert verify_adoption_bundle_owner(bundle, owner_telegram_user_id="123456") == payload


def test_adoption_owner_preflight_accepts_authoritative_canonical_bundle(tmp_path: Path) -> None:
    bundle = build_adoption_bundle(
        AdoptionSectionsV1(
            recipients=[
                AdoptionRecipientV1(
                    chat_id=456789,
                    telegram_user_id=456789,
                    username="recipient",
                    display_name=None,
                    role="recipient",
                ),
                AdoptionRecipientV1(
                    chat_id=123456,
                    telegram_user_id=123456,
                    username="owner",
                    display_name="Owner",
                    role="owner",
                ),
            ]
        ),
        exported_at=datetime(2026, 8, 12, tzinfo=UTC),
        source_fingerprint="a" * 64,
    )
    payload = canonical_bundle_json(bundle).encode("utf-8")
    path = _write(tmp_path / "adoption.json", payload)

    assert verify_adoption_bundle_owner(path, owner_telegram_user_id="123456") == payload


@pytest.mark.parametrize("case", ["owner-mismatch", "missing-owner", "malformed", "hash", "count"])
def test_adoption_owner_preflight_rejects_invalid_bundle_without_values(
    tmp_path: Path,
    case: str,
) -> None:
    recipients = [_owner_recipient()]
    payload: bytes
    secret = "never-print-this-recipient-value"
    recipients[0]["username"] = secret
    if case == "owner-mismatch":
        payload = json.dumps(_adoption_bundle_payload(recipients)).encode()
    elif case == "missing-owner":
        recipients[0]["role"] = "recipient"
        payload = json.dumps(_adoption_bundle_payload(recipients)).encode()
    elif case == "malformed":
        payload = b'{"sections":'
    elif case == "hash":
        document = _adoption_bundle_payload(recipients)
        document["section_sha256"] = {"recipients": "0" * 64}
        payload = json.dumps(document).encode()
    else:
        document = _adoption_bundle_payload(recipients)
        document["entity_counts"] = {"recipients": 2}
        payload = json.dumps(document).encode()
    bundle = _write(tmp_path / "adoption.json", payload)

    with pytest.raises(FbctlError) as raised:
        verify_adoption_bundle_owner(
            bundle, owner_telegram_user_id="654321" if case == "owner-mismatch" else "123456"
        )

    assert str(raised.value) == "adoption bundle owner contract is invalid"
    assert secret not in str(raised.value)


@pytest.mark.parametrize(
    "case",
    [
        "bool-id",
        "bool-user-id",
        "nonpositive-id",
        "unequal-dm",
        "duplicate-id",
        "two-owners",
        "invalid-role",
        "username-type",
        "username-long",
        "display-name-type",
        "display-name-long",
        "unsorted",
        "missing-field",
        "extra-field",
    ],
)
def test_adoption_owner_preflight_requires_exact_recipient_domain_contract(
    tmp_path: Path,
    case: str,
) -> None:
    recipient = _owner_recipient()
    recipients = [recipient]
    expected_owner = "123456"
    if case == "bool-id":
        recipient["chat_id"] = True
    elif case == "bool-user-id":
        recipient["telegram_user_id"] = True
    elif case == "nonpositive-id":
        recipient["chat_id"] = 0
        recipient["telegram_user_id"] = 0
    elif case == "unequal-dm":
        recipient["chat_id"] = 123455
    elif case == "duplicate-id":
        duplicate = _owner_recipient()
        duplicate["role"] = "recipient"
        recipients.append(duplicate)
    elif case == "two-owners":
        recipients.append(_owner_recipient(456789))
    elif case == "invalid-role":
        recipient["role"] = "admin"
    elif case == "username-type":
        recipient["username"] = 123
    elif case == "username-long":
        recipient["username"] = "u" * 65
    elif case == "display-name-type":
        recipient["display_name"] = 123
    elif case == "display-name-long":
        recipient["display_name"] = "d" * 129
    elif case == "unsorted":
        recipient = _owner_recipient(456789)
        lower_recipient = _owner_recipient(123456)
        lower_recipient["role"] = "recipient"
        recipients = [recipient, lower_recipient]
        expected_owner = "456789"
    elif case == "missing-field":
        del recipient["username"]
    else:
        recipient["legacy"] = "not-allowed"
    bundle = _write(
        tmp_path / "adoption.json",
        json.dumps(_adoption_bundle_payload(recipients)).encode(),
    )

    with pytest.raises(FbctlError, match="adoption bundle owner contract is invalid"):
        verify_adoption_bundle_owner(bundle, owner_telegram_user_id=expected_owner)


@pytest.mark.parametrize(
    "case",
    ["wrong-schema", "missing-schema", "symlink", "non-private", "non-regular", "oversize"],
)
def test_adoption_owner_preflight_rejects_unsafe_or_wrong_schema_with_constant_error(
    tmp_path: Path,
    case: str,
) -> None:
    document = _adoption_bundle_payload([_owner_recipient()])
    if case == "wrong-schema":
        document["schema_version"] = "adoption-bundle/v2"
    elif case == "missing-schema":
        del document["schema_version"]
    payload = json.dumps(document).encode()
    path = tmp_path / "adoption.json"
    if case == "symlink":
        path.symlink_to(_write(tmp_path / "target.json", payload))
    elif case == "non-private":
        _write(path, payload, 0o644)
    elif case == "non-regular":
        path.mkdir()
        path.chmod(0o600)
    elif case == "oversize":
        _write(path, b"x" * (MAX_ADOPTION_BUNDLE_BYTES + 1))
    else:
        _write(path, payload)

    with pytest.raises(FbctlError) as raised:
        verify_adoption_bundle_owner(path, owner_telegram_user_id="123456")

    assert str(raised.value) == "adoption bundle owner contract is invalid"
    assert "123456" not in str(raised.value)


def test_adoption_owner_preflight_redacts_recursive_json_failure(tmp_path: Path) -> None:
    payload = b"[" * 2_000 + b"0" + b"]" * 2_000
    path = _write(tmp_path / "adoption.json", payload)

    with pytest.raises(FbctlError) as raised:
        verify_adoption_bundle_owner(path, owner_telegram_user_id="123456")

    assert str(raised.value) == "adoption bundle owner contract is invalid"


def test_vision_folder_id_propagates_only_to_canonical_app_environment(tmp_path: Path) -> None:
    root = _root(tmp_path)
    source = _source_env(tmp_path)
    with source.open("a", encoding="utf-8") as handle:
        handle.write("VISION_FOLDER_ID=folder-current\n")
    source.chmod(0o600)
    release = _materialize(root / "candidate")

    config = prepare_candidate(
        root=root,
        release=release,
        source_env=source,
        docker_config=None,
        adoption_bundle=None,
    )

    assert "VISION_FOLDER_ID=folder-current" in config.layout.app_env.read_text(encoding="utf-8")
    assert "VISION_FOLDER_ID=folder-current" in config.layout.source_env.read_text(encoding="utf-8")


def test_bootstrap_projection_drops_legacy_runtime_values_before_candidate_persistence(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    release = _materialize(root / "candidate")
    source_values = parse_dotenv(root / "shared" / "source.env")
    legacy_values = {
        key: value
        for key, value in _legacy_source_values().items()
        if key in RUNTIME_DERIVED_LEGACY_KEYS
    }
    projected, dropped = project_bootstrap_source(
        source_values | legacy_values,
        project_known_legacy_source=True,
    )
    projected_source = _write(
        tmp_path / "projected.env",
        "".join(f"{key}={value}\n" for key, value in projected.items()),
    )

    config = prepare_candidate(
        root=root,
        release=release,
        source_env=projected_source,
        docker_config=None,
        adoption_bundle=None,
    )

    assert dropped == tuple(sorted(legacy_values))
    assert not set(legacy_values) & set(projected)
    assert {
        key: config.app_values[key]
        for key in (
            "DEV_TOOLS_ENABLED",
            "FRONTEND_ORIGIN",
            "LOG_FORMAT",
            "TRUST_PROXY_HEADERS",
            "WEB_APP_URL",
        )
    } == {
        "DEV_TOOLS_ENABLED": "false",
        "FRONTEND_ORIGIN": "https://app.adpulse.su",
        "LOG_FORMAT": "json",
        "TRUST_PROXY_HEADERS": "true",
        "WEB_APP_URL": "https://app.adpulse.su/tma/",
    }
    assert "SENTRY_ENVIRONMENT" not in config.app_values
    assert "TRACKER_AUTO_CANCEL_ENABLED" not in config.app_values
    assert not set(legacy_values) & set(parse_dotenv(config.layout.source_env))
    persisted_env = "\n".join(
        path.read_text(encoding="utf-8") for path in config.layout.base.rglob("*.env")
    )
    for legacy_value in legacy_values.values():
        assert legacy_value not in persisted_env


def test_missing_bootstrap_keys_are_reported_together() -> None:
    """Один ключ из Vision-пары называет недостающий ключ-партнёра в сообщении об ошибке.

    Было: оба ключа отсутствуют → ошибка с обоими именами.
    Стало: оба отсутствуют — это штатный первый запуск без Vision (ошибки нет).
    Ровно один ключ из пары — это неполный source: ошибка, имя недостающего ключа
    присутствует в тексте, значение sentinel-value не проникает.

    Изменение: тест переориентирован с «оба отсутствуют» на «один из пары», потому
    что новый контракт принимает пустой набор, но отвергает неполный.
    """
    source = {"API_KEY": "sentinel-value", "VISION_X_TOKEN": "some-token"}
    with pytest.raises(FbctlError) as exc_info:
        validate_bootstrap_source_check(source)
    message = str(exc_info.value)
    assert "VISION_PROFILE_ID" in message
    assert "sentinel-value" not in message
    assert "some-token" not in message


def test_missing_required_keys_are_reported_together() -> None:
    """Недостающие обязательные ключи перечисляются одним сообщением, а не по одному.

    Инвариант жил в тесте про Vision-пару, но после #297 пустой набор Vision —
    норма, и проверять его там больше не на чем. Дом инварианта переезжает сюда:
    один круг подъёма стоит полного прогона CI, и отказ обязан назвать всё
    недостающее сразу.
    """
    with pytest.raises(FbctlError) as exc_info:
        canonicalize_source({"API_KEY": "sentinel-value"}, incumbent={})

    message = str(exc_info.value)
    assert "ENCRYPTION_KEY" in message
    assert "TELEGRAM_BOT_TOKEN" in message
    assert "DESKTOP_OWNER_TELEGRAM_USER_ID" in message
    assert "sentinel-value" not in message


def test_vision_bootstrap_absent_keys_accepted_without_error() -> None:
    """Пустой набор Vision-ключей проходит validate_bootstrap_source_check без исключения.

    Инвариант: отсутствие обоих ключей — штатный первый запуск.
    validate_bootstrap_source_check не должна поднимать исключение и не должна
    создавать транспорт; prepare_candidate тоже принимает такой source.
    """
    # Только обязательные для бутстрапа поля, Vision отсутствует.
    source = {"API_KEY": "x"}
    validate_bootstrap_source_check(source)  # не должно бросить исключение


def test_vision_bootstrap_both_keys_present_transport_is_created(tmp_path: Path) -> None:
    """Оба Vision-ключа поданы → транспортный файл создаётся с ожидаемыми полями.

    Поведение при полном наборе должно быть бит-в-бит прежним: транспорт создан,
    ключи VISION_BOOTSTRAP_X_TOKEN / VISION_BOOTSTRAP_PROFILE_ID на месте.
    Тест проходил до правки и должен проходить после.
    """
    root = _root(tmp_path)
    source = _source_env(tmp_path)
    with source.open("a", encoding="utf-8") as handle:
        handle.write("VISION_X_TOKEN=valid-token\n")
        handle.write("VISION_PROFILE_ID=profile-1\n")
    source.chmod(0o600)
    release = _materialize(root / "candidate")

    config = prepare_candidate(
        root=root,
        release=release,
        source_env=source,
        docker_config=None,
        adoption_bundle=tmp_path / "adoption.json",
        bootstrap=True,
    )

    secret_path = Path(config.values["VISION_BOOTSTRAP_ENV_FILE"])
    assert secret_path != Path("/dev/null"), "Транспорт должен быть создан при наличии обоих ключей"
    secret_contents = secret_path.read_text(encoding="utf-8")
    assert "VISION_BOOTSTRAP_X_TOKEN=valid-token" in secret_contents
    assert "VISION_BOOTSTRAP_PROFILE_ID=profile-1" in secret_contents
    # Исходные имена ключей не должны попасть в транспорт (только переименованные)
    assert "VISION_X_TOKEN=" not in secret_contents


def test_vision_bootstrap_half_pair_and_invalid_profile_are_rejected(tmp_path: Path) -> None:
    """Один ключ из пары и негодный формат профиля отвергаются.

    Случаи:
    1. validate_bootstrap_source_check с одним ключом → FbctlError (имя недостающего есть).
    2. validate_bootstrap_source_check с обоими ключами, но негодным VISION_PROFILE_ID →
       FbctlError (профиль не по маске).
    3. prepare_candidate с одним ключом → FbctlError.
    """
    # Случай 1: один ключ в validate_bootstrap_source_check.
    with pytest.raises(FbctlError) as exc_info:
        validate_bootstrap_source_check({"API_KEY": "x", "VISION_X_TOKEN": "abc"})
    assert "VISION_PROFILE_ID" in str(exc_info.value)

    # Случай 2: оба ключа, но профиль не по маске (кириллица не входит в [A-Za-z0-9._:-]).
    with pytest.raises(FbctlError, match="Vision credentials are invalid"):
        validate_bootstrap_source_check(
            {
                "API_KEY": "x",
                "VISION_X_TOKEN": "abc",
                "VISION_PROFILE_ID": "не-годится",
            }
        )

    # Случай 3: один ключ в prepare_candidate (bootstrap=True).
    root = _root(tmp_path)
    source = _source_env(tmp_path)
    with source.open("a", encoding="utf-8") as handle:
        handle.write("VISION_PROFILE_ID=profile-1\n")  # только один ключ из пары
    source.chmod(0o600)
    release = _materialize(root / "candidate")

    with pytest.raises(FbctlError) as exc_info:
        prepare_candidate(
            root=root,
            release=release,
            source_env=source,
            docker_config=None,
            adoption_bundle=tmp_path / "adoption.json",
            bootstrap=True,
        )
    assert "VISION_X_TOKEN" in str(exc_info.value)


def test_bootstrap_source_check_is_in_memory_and_redacts_values(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = {
        **{
            line.split("=", 1)[0]: line.split("=", 1)[1]
            for line in _source_env(tmp_path).read_text(encoding="utf-8").splitlines()
        },
        **_legacy_source_values(),
        "VISION_X_TOKEN": "vision-super-secret",
        "VISION_PROFILE_ID": "profile-1",
    }
    payload = "".join(f"{key}={value}\n" for key, value in source.items()).encode()
    stdin = io.TextIOWrapper(io.BytesIO(payload), encoding="utf-8")
    monkeypatch.setattr("sys.stdin", stdin)
    before = list(tmp_path.rglob("*"))

    assert (
        fbctl_main.main(["bootstrap-source-check", "--stdin", "--project-known-legacy-source"]) == 0
    )

    output = capsys.readouterr().out
    assert '"status": "READY"' in output
    assert "API_HOST" in output
    assert "vision-super-secret" not in output
    assert "super-secret@" not in output
    for legacy_key in RUNTIME_DERIVED_LEGACY_KEYS:
        assert _legacy_source_values()[legacy_key] not in output
    assert list(tmp_path.rglob("*")) == before


def test_bootstrap_source_check_rejects_duplicate_dotenv_in_memory(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    stdin = io.TextIOWrapper(io.BytesIO(b"API_KEY=one\nAPI_KEY=two\n"), encoding="utf-8")
    monkeypatch.setattr("sys.stdin", stdin)

    assert (
        fbctl_main.main(["bootstrap-source-check", "--stdin", "--project-known-legacy-source"]) == 1
    )

    assert "duplicate API_KEY" in capsys.readouterr().err


def test_bootstrap_vision_secrets_never_enter_canonical_runtime_env(tmp_path: Path) -> None:
    root = _root(tmp_path)
    source = _source_env(tmp_path)
    with source.open("a", encoding="utf-8") as handle:
        handle.write("VISION_X_TOKEN=vision-secret\n")
        handle.write("VISION_PROFILE_ID=profile-1\n")
        handle.write("PANEL_BASIC_AUTH_USER=owner\n")
        handle.write("PANEL_BASIC_AUTH_HASH=$2b$12$" + "a" * 53 + "\n")
    source.chmod(0o600)
    release = _materialize(root / "candidate")

    config = prepare_candidate(
        root=root,
        release=release,
        source_env=source,
        docker_config=None,
        adoption_bundle=tmp_path / "adoption.json",
        bootstrap=True,
    )

    canonical = config.layout.source_env.read_text(encoding="utf-8")
    app = config.layout.app_env.read_text(encoding="utf-8")
    secret = Path(config.values["VISION_BOOTSTRAP_ENV_FILE"]).read_text(encoding="utf-8")
    assert "VISION_X_TOKEN" not in canonical + app
    assert "PANEL_BASIC_AUTH" not in canonical + app
    assert "VISION_BOOTSTRAP_X_TOKEN=vision-secret" in secret


def test_vision_desktop_environment_is_strictly_scoped(tmp_path: Path) -> None:
    root = _root(tmp_path)
    release = _materialize(root / "candidate")

    config = prepare_candidate(
        root=root,
        release=release,
        source_env=None,
        docker_config=None,
        adoption_bundle=None,
    )

    desktop = Path(config.values["DESKTOP_ENV_FILE"])
    assert desktop == root / "candidate" / "secrets" / "desktop.env"
    assert desktop.stat().st_mode & 0o777 == 0o600
    values = desktop.read_text(encoding="utf-8")
    assert set(line.split("=", 1)[0] for line in values.splitlines()) == {
        "DESKTOP_RUSTDESK_PASSWORD",
        "DESKTOP_RUSTDESK_SERVER",
    }
    # Пароль сгенерирован fbctl и достаточно длинный, адрес имеет дефолт.
    parsed = dict(line.split("=", 1) for line in values.splitlines())
    assert len(parsed["DESKTOP_RUSTDESK_PASSWORD"]) >= 32
    assert parsed["DESKTOP_RUSTDESK_SERVER"]
    for forbidden in (
        "TELEGRAM_BOT_TOKEN",
        "POSTGRES_PASSWORD",
        "API_KEY",
        "ENCRYPTION_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
        "KASM",
    ):
        assert forbidden not in values


def test_active_runtime_rejects_desktop_environment_outside_canonical_path(tmp_path: Path) -> None:
    root = _root(tmp_path)
    release = _materialize(root / "candidate")
    prepare_candidate(
        root=root,
        release=release,
        source_env=None,
        docker_config=None,
        adoption_bundle=None,
    )
    payload = root / ".runtime-test"
    os.replace(root / "candidate", payload)
    (root / "runtime").symlink_to(payload.name)
    runtime_env = payload / "runtime.env"
    runtime_env.chmod(0o600)
    runtime_values = runtime_env.read_text(encoding="utf-8").replace(
        os.fspath(root / "candidate"), os.fspath(payload)
    )
    runtime_env.write_text(
        runtime_values.replace(
            f"DESKTOP_ENV_FILE={payload / 'secrets' / 'desktop.env'}",
            "DESKTOP_ENV_FILE=/tmp/desktop.env",
        ),
        encoding="utf-8",
    )
    runtime_env.chmod(0o400)

    with pytest.raises(FbctlError, match="non-canonical desktop environment"):
        load_active(root)


def test_bootstrap_cleans_temporary_secret_and_partial_candidate_on_prepare_failure(
    tmp_path: Path,
    monkeypatch,
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    root = tmp_path / "fb-agent"
    _write_managed_vision_profile(root)
    (root / "shared").chmod(0o700)
    source = _source_env(tmp_path)
    with source.open("a", encoding="utf-8") as handle:
        handle.write("VISION_X_TOKEN=vision-secret\n")
        handle.write("VISION_PROFILE_ID=profile-1\n")
    source.chmod(0o600)

    monkeypatch.setattr(
        "fbctl.controller._normalize_profile_tree",
        lambda _path, *, uid, gid: None,
    )

    def fail_materialize(destination: Path) -> dict[str, object]:
        destination.mkdir(mode=0o700)
        _write(destination / "partial", "partial\n")
        raise FbctlError("materialize failed")

    monkeypatch.setattr("fbctl.controller.materialize_candidate", fail_materialize)

    with pytest.raises(FbctlError, match="materialize failed"):
        bootstrap_host(
            runner=FakeRunner(),
            root=root,
            source_env=source,
            adoption_bundle=None,
            desktop_profile_seed=None,
            docker_config=None,
            rehearsal=True,
        )

    assert not list((root / "shared").glob(".bootstrap-source-*.env"))
    assert not (root / "candidate").exists()


def test_invalid_bootstrap_only_transport_never_persists_canonical_source(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    root = tmp_path / "fb-agent"
    source = _source_env(tmp_path)
    with source.open("a", encoding="utf-8") as handle:
        handle.write(
            "VISION_X_TOKEN=vision-secret\n"
            "VISION_PROFILE_ID=profile-1\n"
            "PANEL_BASIC_AUTH_USER=owner\n"
            "PANEL_BASIC_AUTH_HASH=not-a-bcrypt-hash\n"
        )
    source.chmod(0o600)

    with pytest.raises(FbctlError, match="Caddy panel credentials are invalid"):
        bootstrap_host(
            runner=FakeRunner(),
            root=root,
            source_env=source,
            adoption_bundle=None,
            desktop_profile_seed=None,
            docker_config=None,
        )

    assert not (root / "shared" / "source.env").exists()
    assert not (root / "candidate").exists()


def _root_owned_stat(path: Path, monkeypatch: pytest.MonkeyPatch, owner: int = 0) -> None:
    original = Path.stat

    def stat_as_owner(candidate: Path, *args, **kwargs):
        result = original(candidate, *args, **kwargs)
        if candidate == path:
            fields = list(result)
            fields[4] = owner
            return os.stat_result(fields)
        return result

    monkeypatch.setattr(Path, "stat", stat_as_owner)


def test_bootstrap_reuses_only_valid_existing_caddy_panel_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    caddy_env = _write(
        tmp_path / "caddy.env",
        "PANEL_BASIC_AUTH_USER=owner\n"
        "PANEL_BASIC_AUTH_HASH=$2b$12$" + "a" * 53 + "\n"
        "API_KEY=must-not-be-imported\n"
        "DESKTOP_KASM_SERVICE_AUTH_B64=must-not-be-imported\n",
    )
    monkeypatch.setattr(fbctl_controller, "CADDY_ENV_PATH", caddy_env)
    _root_owned_stat(caddy_env, monkeypatch)

    values = fbctl_controller._resolve_caddy_bootstrap_credentials(
        {}, provision_caddy=True, reuse_existing=True
    )
    assert values == {
        "PANEL_BASIC_AUTH_USER": "owner",
        "PANEL_BASIC_AUTH_HASH": "$2b$12$" + "a" * 53,
    }
    assert "API_KEY" not in values
    assert "DESKTOP_KASM_SERVICE_AUTH_B64" not in values


@pytest.mark.parametrize(
    "case,caddy_contents,mode,owner,error",
    [
        ("missing", None, 0o600, 0, "required private file is missing"),
        ("symlink", None, 0o600, 0, "required private file is unsafe"),
        (
            "invalid",
            "PANEL_BASIC_AUTH_USER=owner\nPANEL_BASIC_AUTH_HASH=bad\n",
            0o600,
            0,
            "Caddy panel credentials are invalid",
        ),
        ("partial", "PANEL_BASIC_AUTH_USER=owner\n", 0o600, 0, "missing panel keys"),
        (
            "duplicate",
            "PANEL_BASIC_AUTH_USER=owner\nPANEL_BASIC_AUTH_USER=again\n",
            0o600,
            0,
            "duplicate",
        ),
        (
            "mode",
            "PANEL_BASIC_AUTH_USER=owner\nPANEL_BASIC_AUTH_HASH=$2b$12$" + "a" * 53 + "\n",
            0o644,
            0,
            "mode 600",
        ),
        (
            "owner",
            "PANEL_BASIC_AUTH_USER=owner\nPANEL_BASIC_AUTH_HASH=$2b$12$" + "a" * 53 + "\n",
            0o600,
            501,
            "owned by root",
        ),
    ],
)
def test_bootstrap_reuse_rejects_invalid_existing_caddy_env_before_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: str,
    caddy_contents: str | None,
    mode: int,
    owner: int,
    error: str,
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    source = _source_env(tmp_path)
    with source.open("a", encoding="utf-8") as handle:
        handle.write("VISION_X_TOKEN=vision-secret\nVISION_PROFILE_ID=profile-1\n")
    caddy_env = tmp_path / "caddy.env"
    if case == "symlink":
        caddy_env.symlink_to(_write(tmp_path / "caddy-target.env", "irrelevant\n"))
    elif caddy_contents is not None:
        _write(caddy_env, caddy_contents, mode)
    monkeypatch.setattr(fbctl_controller, "CADDY_ENV_PATH", caddy_env)
    if case not in {"missing", "symlink"}:
        _root_owned_stat(caddy_env, monkeypatch, owner)
    root = tmp_path / "fb-agent"
    with pytest.raises(FbctlError, match=error):
        bootstrap_host(
            runner=FakeRunner(),
            root=root,
            source_env=source,
            adoption_bundle=None,
            desktop_profile_seed=None,
            docker_config=None,
            reuse_existing_caddy_credentials=True,
        )
    assert not (root / "shared" / "source.env").exists()
    assert not (root / "candidate").exists()


def test_bootstrap_rejects_partial_or_ambiguous_caddy_source_before_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    source = _source_env(tmp_path)
    with source.open("a", encoding="utf-8") as handle:
        handle.write(
            "VISION_X_TOKEN=vision-secret\nVISION_PROFILE_ID=profile-1\n"
            "PANEL_BASIC_AUTH_USER=owner\n"
        )
    source.chmod(0o600)
    root = tmp_path / "fb-agent"

    with pytest.raises(FbctlError, match="both panel keys or neither"):
        bootstrap_host(
            runner=FakeRunner(),
            root=root,
            source_env=source,
            adoption_bundle=None,
            desktop_profile_seed=None,
            docker_config=None,
            reuse_existing_caddy_credentials=True,
        )
    assert not (root / "shared" / "source.env").exists()
    assert not (root / "candidate").exists()


def test_explicit_caddy_pair_wins_when_reuse_fallback_is_enabled(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    source = _source_env(tmp_path)
    with source.open("a", encoding="utf-8") as handle:
        handle.write(
            "VISION_X_TOKEN=vision-secret\nVISION_PROFILE_ID=profile-1\n"
            "PANEL_BASIC_AUTH_USER=owner\n"
            "PANEL_BASIC_AUTH_HASH=$2b$12$" + "a" * 53 + "\n"
        )
    source.chmod(0o600)
    values = fbctl_controller._resolve_caddy_bootstrap_credentials(
        parse_dotenv(source), provision_caddy=True, reuse_existing=True
    )
    assert values == {
        "PANEL_BASIC_AUTH_USER": "owner",
        "PANEL_BASIC_AUTH_HASH": "$2b$12$" + "a" * 53,
    }


def _map_caddy_host(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Path]:
    real_path = Path
    mapped_paths = {
        "/etc/caddy/Caddyfile": tmp_path / "etc/caddy/Caddyfile",
        "/etc/fb-agent/caddy.env": tmp_path / "etc/fb-agent/caddy.env",
        "/etc/caddy/sites-enabled": tmp_path / "etc/caddy/sites-enabled",
        "/etc/systemd/system/caddy.service.d": (tmp_path / "etc/systemd/system/caddy.service.d"),
        "/var/log/caddy": tmp_path / "var/log/caddy",
    }

    def mapped_path(value: object) -> Path:
        rendered = os.fspath(value)
        return mapped_paths.get(rendered, real_path(rendered))

    monkeypatch.setattr(fbctl_controller, "Path", mapped_path)
    monkeypatch.setattr(
        fbctl_controller,
        "CADDY_ENV_PATH",
        mapped_paths["/etc/fb-agent/caddy.env"],
    )
    monkeypatch.setattr(
        fbctl_controller.pwd,
        "getpwnam",
        lambda name: SimpleNamespace(pw_uid=os.getuid()) if name == "caddy" else None,
    )
    monkeypatch.setattr(
        fbctl_controller.grp,
        "getgrnam",
        lambda name: SimpleNamespace(gr_gid=os.getgid()) if name == "caddy" else None,
    )
    return mapped_paths


def test_fresh_caddy_provisioning_creates_every_host_file_and_is_repeatable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mapped_paths = _map_caddy_host(tmp_path, monkeypatch)
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    resources = tmp_path / "resources"
    _materialize(resources)
    source_values = canonicalize_source(parse_dotenv(_source_env(tmp_path)), incumbent={})
    bootstrap_values = {
        "PANEL_BASIC_AUTH_USER": "owner",
        "PANEL_BASIC_AUTH_HASH": "$2b$12$" + "a" * 53,
    }
    runner = FakeRunner()

    fbctl_controller._provision_caddy(  # noqa: SLF001 - exact cold host seam
        resources,
        source_values,
        bootstrap_values,
        runner,
    )
    first_files = {
        path: path.read_bytes()
        for path in (
            mapped_paths["/etc/caddy/Caddyfile"],
            mapped_paths["/etc/fb-agent/caddy.env"],
            mapped_paths["/etc/caddy/sites-enabled"] / "app.adpulse.su.caddy",
            mapped_paths["/etc/systemd/system/caddy.service.d"] / "fb-agent-env.conf",
        )
    }
    # Model a process death after only part of the host state became durable.
    for path in (
        mapped_paths["/etc/fb-agent/caddy.env"],
        mapped_paths["/etc/caddy/sites-enabled"] / "app.adpulse.su.caddy",
        mapped_paths["/etc/systemd/system/caddy.service.d"] / "fb-agent-env.conf",
        mapped_paths["/var/log/caddy"] / "fb-agent-desktop-access.log",
    ):
        path.unlink()

    fbctl_controller._provision_caddy(  # noqa: SLF001 - cold/hot parity
        resources,
        source_values,
        bootstrap_values,
        runner,
    )

    assert first_files == {path: path.read_bytes() for path in first_files}
    assert first_files[mapped_paths["/etc/caddy/Caddyfile"]] == (
        b"import /etc/caddy/sites-enabled/*.caddy\n"
    )
    for name in ("fb-agent-access.log", "fb-agent-desktop-access.log"):
        log = mapped_paths["/var/log/caddy"] / name
        assert log.is_file()
        assert stat.S_IMODE(log.stat().st_mode) == 0o600
    commands = [command for step, command in runner.commands if step == "bootstrap"]
    assert len(commands) == 8
    live_validation = (
        "caddy",
        "validate",
        "--config",
        os.fspath(mapped_paths["/etc/caddy/Caddyfile"]),
        "--adapter",
        "caddyfile",
        "--envfile",
        os.fspath(mapped_paths["/etc/fb-agent/caddy.env"]),
    )
    for offset in (0, 4):
        staged_validation = commands[offset]
        assert staged_validation[:3] == ("caddy", "validate", "--config")
        assert ".fb-agent-caddy-" in staged_validation[3]
        assert commands[offset + 1] == live_validation
        assert commands[offset + 2] == ("systemctl", "daemon-reload")
        assert commands[offset + 3] == ("systemctl", "reload", "caddy")


def test_caddy_reload_failure_restores_previous_managed_host_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    mapped_paths = _map_caddy_host(tmp_path, monkeypatch)
    caddy_file = _write(
        mapped_paths["/etc/caddy/Caddyfile"],
        "import /etc/caddy/sites-enabled/*.caddy\n",
        0o644,
    )
    sites = mapped_paths["/etc/caddy/sites-enabled"]
    app = _write(sites / "app.adpulse.su.caddy", "old app config\n", 0o644)
    desktop = _write(sites / "desktop.adpulse.su.caddy", "old desktop config\n", 0o644)
    dropin = _write(
        mapped_paths["/etc/systemd/system/caddy.service.d"] / "fb-agent-env.conf",
        "old drop-in\n",
        0o644,
    )
    previous = {target: target.read_bytes() for target in (caddy_file, app, desktop, dropin)}
    resources = tmp_path / "resources"
    _materialize(resources)
    source_values = canonicalize_source(parse_dotenv(_source_env(tmp_path)), incumbent={})
    panel_values = {
        "PANEL_BASIC_AUTH_USER": "owner",
        "PANEL_BASIC_AUTH_HASH": "$2b$12$" + "a" * 53,
    }

    class FailFirstReloadRunner(FakeRunner):
        reload_failed = False

        def run(self, command, **kwargs):
            result = super().run(command, **kwargs)
            argv = tuple(os.fspath(part) for part in command)
            if argv == ("systemctl", "reload", "caddy") and not self.reload_failed:
                self.reload_failed = True
                raise FbctlError("injected Caddy reload failure")
            return result

    runner = FailFirstReloadRunner()

    with pytest.raises(FbctlError, match="injected Caddy reload failure"):
        fbctl_controller._reconcile_caddy(  # noqa: SLF001 - transactional host seam
            resources,
            source_values,
            panel_values,
            runner,
            step="sync_caddy",
        )

    assert previous == {target: target.read_bytes() for target in previous}
    assert not mapped_paths["/etc/fb-agent/caddy.env"].exists()
    assert [
        command
        for step, command in runner.commands
        if step == "sync_caddy" and command == ("systemctl", "reload", "caddy")
    ] == [("systemctl", "reload", "caddy")] * 2


def test_first_bootstrap_creates_host_tree_and_copies_external_profile_seed(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    # Production uses /opt/fb-agent: the fixed parent exists, the managed root
    # itself does not.
    root = tmp_path / "fb-agent"
    source = _source_env(tmp_path)
    with source.open("a", encoding="utf-8") as handle:
        handle.write("VISION_X_TOKEN=vision-secret\nVISION_PROFILE_ID=profile-1\n")
    source.chmod(0o600)
    seed = _write_managed_vision_profile(tmp_path / "external-seed")
    monkeypatch.setattr(fbctl_controller, "materialize_candidate", _materialize)
    for method in (
        "_preflight",
        "_pull",
        "_ensure_bootstrap_resources",
        "_start_infra",
        "_migrate",
        "_bootstrap_adoption",
        "_bootstrap_runtime_config",
        "_bootstrap_vision_config",
    ):
        monkeypatch.setattr(ProductionController, method, lambda *_args, **_kwargs: None)

    result = bootstrap_host(
        runner=FakeRunner(),
        root=root,
        source_env=source,
        adoption_bundle=None,
        desktop_profile_seed=seed,
        docker_config=None,
        rehearsal=True,
    )

    canonical = parse_dotenv(root / "shared" / "source.env")
    assert result["status"] == "READY"
    assert result["profile_seed_cleanup"] == "removed"
    assert stat.S_IMODE(root.stat().st_mode) == 0o755
    assert stat.S_IMODE((root / "shared").stat().st_mode) == 0o700
    assert stat.S_IMODE((root / "shared" / "deploy.lock").stat().st_mode) == 0o600
    assert re.fullmatch(r"[0-9a-f]{32}", canonical["FB_AGENT_BOOTSTRAP_CLUSTER_ID"])
    assert len(canonical["POSTGRES_PASSWORD"]) >= 16
    assert len(canonical["DESKTOP_RUSTDESK_PASSWORD"]) >= 32
    assert (root / "shared" / "vision-config" / VISION_PROFILE_MARKER).is_file()
    assert not seed.exists()


def test_bootstrap_refuses_non_root_before_durable_writes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = tmp_path / "fb-agent"
    source = _source_env(tmp_path)
    monkeypatch.setattr(os, "geteuid", lambda: 501)

    with pytest.raises(FbctlError, match="root privileges"):
        bootstrap_host(
            runner=FakeRunner(),
            root=root,
            source_env=source,
            adoption_bundle=None,
            desktop_profile_seed=None,
            docker_config=None,
            rehearsal=True,
        )

    assert not root.exists()


def test_bootstrap_owner_mismatch_makes_no_durable_writes_or_runner_calls(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    monkeypatch.setattr(fbctl_controller.sys, "version_info", (3, 12))
    root = tmp_path / "fb-agent"
    bundle = _write(
        tmp_path / "adoption.json",
        json.dumps(_adoption_bundle_payload([_owner_recipient(654321)])).encode(),
    )
    runner = FakeRunner()

    with pytest.raises(FbctlError, match="adoption bundle owner contract is invalid"):
        bootstrap_host(
            runner=runner,
            root=root,
            source_env=_source_env(tmp_path),
            adoption_bundle=bundle,
            desktop_profile_seed=None,
            docker_config=None,
            rehearsal=True,
        )

    assert not root.exists()
    assert runner.commands == []


@pytest.mark.parametrize(
    ("fail_import", "replace_source"),
    [(False, False), (False, True), (True, False), (True, True)],
)
def test_bootstrap_imports_only_verified_candidate_bundle_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_import: bool,
    replace_source: bool,
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    root = tmp_path / "fb-agent"
    _write_managed_vision_profile(root)
    (root / "shared").chmod(0o700)
    source = _source_env(tmp_path)
    with source.open("a", encoding="utf-8") as handle:
        handle.write("VISION_X_TOKEN=vision-secret\nVISION_PROFILE_ID=profile-1\n")
    source.chmod(0o600)
    verified_payload = json.dumps(_adoption_bundle_payload([_owner_recipient()])).encode()
    original_bundle = _write(
        root / "shared" / "adoption-bundle-v1.json",
        verified_payload,
    )
    tampered_payload = b'{"tampered":true}'

    monkeypatch.setattr(fbctl_controller, "materialize_candidate", _materialize)
    monkeypatch.setattr(
        fbctl_controller,
        "_normalize_profile_tree",
        lambda _path, *, uid, gid: None,
    )
    for method in (
        "_preflight",
        "_pull",
        "_ensure_bootstrap_resources",
        "_start_infra",
        "_migrate",
        "_bootstrap_runtime_config",
        "_bootstrap_vision_config",
    ):
        monkeypatch.setattr(ProductionController, method, lambda *_args, **_kwargs: None)

    captured: dict[str, object] = {}

    def capture_adoption(
        _controller: ProductionController,
        config,
        adoption_bundle: Path | None,
    ) -> None:
        assert adoption_bundle is not None
        if replace_source:
            replacement = _write(root / "shared" / ".adoption.next.json", tampered_payload)
            os.replace(replacement, original_bundle)
        captured["argument"] = adoption_bundle
        captured["runtime"] = Path(config.values["ADOPTION_BUNDLE_FILE"])
        captured["payload"] = adoption_bundle.read_bytes()
        captured["mode"] = adoption_bundle.stat().st_mode & 0o777
        captured["parent_mode"] = adoption_bundle.parent.stat().st_mode & 0o777
        if fail_import:
            raise FbctlError("injected adoption failure")

    monkeypatch.setattr(ProductionController, "_bootstrap_adoption", capture_adoption)
    runner = FakeRunner()

    kwargs = {
        "runner": runner,
        "root": root,
        "source_env": source,
        "adoption_bundle": original_bundle,
        "desktop_profile_seed": None,
        "docker_config": None,
        "rehearsal": True,
    }
    if fail_import:
        with pytest.raises(FbctlError, match="injected adoption failure"):
            bootstrap_host(**kwargs)
        result = None
    else:
        result = bootstrap_host(**kwargs)

    snapshot = captured["argument"]
    assert isinstance(snapshot, Path)
    if result is not None:
        assert result["status"] == "READY"
    assert captured["runtime"] == snapshot
    assert snapshot == root / "candidate" / "secrets" / "adoption-bundle-v1.json"
    assert snapshot != original_bundle
    assert captured["payload"] == verified_payload
    assert captured["payload"] != tampered_payload
    assert captured["mode"] == 0o600
    assert captured["parent_mode"] == 0o700
    if replace_source:
        assert original_bundle.read_bytes() == tampered_payload
    elif fail_import:
        assert original_bundle.read_bytes() == verified_payload
    else:
        assert not original_bundle.exists()
    assert not snapshot.exists()
    assert not (root / "candidate").exists()
    assert runner.commands == [
        ("preflight", ("docker", "container", "ls", "--all", "--quiet", "--no-trunc"))
    ]


def test_deploy_orders_webhook_before_workers_and_promotes_after_all_evidence(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    canonical_source = (root / "shared" / "source.env").read_bytes()
    now = time.time()
    runner = FakeRunner(now=now)
    log: list[str] = []
    controller = ProductionController(
        runner=runner,
        probes=FakeProbes(),
        materialize=_materialize,
        now=lambda: now,
        sleep=lambda _seconds: None,
        log=log.append,
    )

    result = controller.deploy(DeployOptions(root=root, rehearsal=True, enable_scanning=True))

    assert result.status == "READY"
    assert (root / "runtime").is_symlink()
    assert not (root / "candidate").exists()
    assert not (root / "releases").exists()
    assert not (root / "current").exists()
    runtime_env = (root / "runtime" / "runtime.env").read_text(encoding="utf-8")
    assert f"APP_ENV_FILE={(root / 'runtime').resolve() / 'app.env'}" in runtime_env
    assert f"APP_ENV_FILE={root / 'candidate' / 'app.env'}" not in runtime_env
    assert (root / "runtime" / "deploy-state.json").is_file()
    assert (root / "shared" / "source.env").read_bytes() == canonical_source
    assert load_active(root).layout.base == (root / "runtime").resolve()
    assert (
        len(
            [
                command
                for _step, command in runner.commands
                if "http://127.0.0.1:9464/metrics" in " ".join(command)
            ]
        )
        == 11
    )
    rendered = [" ".join(command) for _step, command in runner.commands]
    assert not any("adoption_importer" in command for command in rendered)
    assert not any("runtime_config_bootstrap" in command for command in rendered)
    completed = [
        message.removeprefix("[fbctl] step=").removesuffix(" completed")
        for message in log
        if message.startswith("[fbctl] step=") and message.endswith(" completed")
    ]
    assert completed == list(REHEARSAL_FAILPOINTS)
    assert completed.index("configure_telegram_webhook") < completed.index("start_workers")
    assert completed.index("verify_telegram_webhook") < completed.index("start_workers")
    assert completed.index("start_workers") < completed.index("verify_worker_heartbeats")
    assert completed.index("verify_worker_heartbeats") < completed.index("verify_system_ready")
    deploy_state = json.loads((root / "runtime" / "deploy-state.json").read_text(encoding="utf-8"))
    assert deploy_state["completed_steps"] == [*REHEARSAL_FAILPOINTS, "promote"]


def test_real_deploy_reconciles_stale_managed_caddy_config_before_public_smoke(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    mapped_paths = _map_caddy_host(tmp_path, monkeypatch)
    _write(
        mapped_paths["/etc/caddy/Caddyfile"],
        "import /etc/caddy/sites-enabled/*.caddy\n",
        0o644,
    )
    sites = mapped_paths["/etc/caddy/sites-enabled"]
    _write(
        sites / "app.adpulse.su.caddy",
        (ROOT / "deploy/caddy/app.adpulse.su.caddy").read_bytes(),
        0o644,
    )
    stale_desktop = _write(
        sites / "desktop.adpulse.su.caddy",
        'desktop.adpulse.su { respond "stale host config" }\n',
        0o644,
    )
    unmanaged_site = _write(
        sites / "other.example.caddy",
        'other.example { respond "managed elsewhere" }\n',
        0o640,
    )
    unmanaged_snapshot = unmanaged_site.read_bytes()
    _write(
        mapped_paths["/etc/systemd/system/caddy.service.d"] / "fb-agent-env.conf",
        (ROOT / "deploy/systemd/caddy-fb-agent-env.conf").read_bytes(),
        0o644,
    )

    def prepare_with_host_caddy(**kwargs) -> object:
        config = prepare_candidate(**kwargs)
        caddy_values = {
            "PANEL_BASIC_AUTH_USER": "owner",
            "PANEL_BASIC_AUTH_HASH": "$2b$12$" + "a" * 53,
        }
        fbctl_controller._sync_caddy_values(  # noqa: SLF001 - exact deploy host seam
            {**config.app_values, **config.desktop_values},
            caddy_values,
        )
        _write(
            mapped_paths["/etc/fb-agent/caddy.env"],
            "".join(f"{key}={value}\n" for key, value in caddy_values.items()),
        )
        return config

    now = time.time()
    runner = FakeRunner(now=now)
    log: list[str] = []
    controller = ProductionController(
        runner=runner,
        probes=FakeProbes(),
        materialize=_materialize,
        prepare=prepare_with_host_caddy,
        now=lambda: now,
        sleep=lambda _seconds: None,
        log=log.append,
    )

    assert controller.deploy(DeployOptions(root=root)).status == "READY"

    # Ретированный сайт стола деплой удаляет: Caddyfile импортирует каталог
    # целиком, и оставленный файл продолжил бы обслуживаться.
    assert not stale_desktop.exists()
    assert unmanaged_site.read_bytes() == unmanaged_snapshot
    assert stat.S_IMODE(unmanaged_site.stat().st_mode) == 0o640
    assert ("sync_caddy", ("systemctl", "reload", "caddy")) in runner.commands
    assert not any(command[:2] == ("caddy", "reload") for _step, command in runner.commands)
    completed = [
        message.removeprefix("[fbctl] step=").removesuffix(" completed")
        for message in log
        if message.startswith("[fbctl] step=") and message.endswith(" completed")
    ]
    assert completed.index("sync_caddy") < completed.index("public_smoke")


def test_real_deploy_rolls_caddy_back_when_post_reload_smoke_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    rollback_steps: list[str] = []

    class CaddyChange:
        @staticmethod
        def rollback(_runner, *, step: str) -> None:
            rollback_steps.append(step)

    def fail_public_smoke(_controller, _config) -> None:
        raise FbctlError("injected public smoke failure")

    monkeypatch.setattr(
        ProductionController,
        "_require_caddy_credentials",
        lambda *_args: None,
    )
    monkeypatch.setattr(
        ProductionController,
        "_sync_caddy",
        lambda *_args: CaddyChange(),
    )
    monkeypatch.setattr(ProductionController, "_public_smoke", fail_public_smoke)
    monkeypatch.setattr(
        fbctl_controller,
        "_retire_legacy_systemd_units",
        lambda *_args, **_kwargs: [],
    )
    controller = ProductionController(
        runner=FakeRunner(),
        probes=FakeProbes(),
        materialize=_materialize,
        sleep=lambda _seconds: None,
    )

    with pytest.raises(FbctlError, match="injected public smoke failure"):
        controller.deploy(DeployOptions(root=root))

    assert rollback_steps == ["sync_caddy_rollback"]


def test_real_deploy_retires_legacy_systemd_after_pull_before_runtime_stop(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    log: list[str] = []
    retirement_steps: list[str] = []

    def retire(_runner, *, step: str) -> list[str]:
        retirement_steps.append(step)
        return []

    monkeypatch.setattr(fbctl_controller, "_retire_legacy_systemd_units", retire)
    monkeypatch.setattr(
        ProductionController,
        "_require_caddy_credentials",
        lambda *_args: None,
    )
    monkeypatch.setattr(ProductionController, "_sync_caddy", lambda *_args: None)
    controller = ProductionController(
        runner=FakeRunner(),
        probes=FakeProbes(),
        materialize=_materialize,
        now=time.time,
        sleep=lambda _seconds: None,
        log=log.append,
    )

    assert controller.deploy(DeployOptions(root=root)).status == "READY"

    completed = [
        message.removeprefix("[fbctl] step=").removesuffix(" completed")
        for message in log
        if message.startswith("[fbctl] step=") and message.endswith(" completed")
    ]
    assert retirement_steps == ["retire_legacy_systemd"]
    assert completed.index("pull") < completed.index("retire_legacy_systemd")
    assert completed.index("retire_legacy_systemd") < completed.index("stop_runtime")


def test_post_migration_failpoint_keeps_incumbent_runtime_and_fences_workers(
    tmp_path: Path,
) -> None:
    root = _root(tmp_path)
    incumbent = root / "runtime"
    incumbent.mkdir(mode=0o700)
    _write(incumbent / "marker", "incumbent\n", 0o600)
    runner = FakeRunner()
    controller = ProductionController(
        runner=runner,
        probes=FakeProbes(),
        materialize=_materialize,
    )

    with pytest.raises(FbctlError, match="failpoint"):
        controller.deploy(DeployOptions(root=root, rehearsal=True, fail_after_step="migrate"))

    assert (incumbent / "marker").read_text(encoding="utf-8") == "incumbent\n"
    assert any(step == "failure_cleanup" and "stop" in command for step, command in runner.commands)


def test_before_promote_failpoint_is_the_last_failure_class_point(tmp_path: Path) -> None:
    assert REHEARSAL_FAILPOINTS[-1] == "before_promote"
    assert "promote" not in REHEARSAL_FAILPOINTS
    root = _root(tmp_path)
    runner = FakeRunner()
    controller = ProductionController(
        runner=runner,
        probes=FakeProbes(),
        materialize=_materialize,
        sleep=lambda _seconds: None,
    )

    with pytest.raises(FbctlError, match="failpoint"):
        controller.deploy(
            DeployOptions(root=root, rehearsal=True, fail_after_step="before_promote")
        )

    assert not (root / "runtime").exists()
    assert (root / "candidate").is_dir()
    assert any(step == "failure_cleanup" and "stop" in command for step, command in runner.commands)


@pytest.mark.parametrize(
    "stage",
    (
        "copy_candidate",
        "write_runtime_env",
        "write_deploy_state",
        "prepare_runtime_pointer",
        "commit_runtime_pointer",
    ),
)
def test_promotion_filesystem_failure_before_pointer_commit_fences_workers(
    tmp_path: Path,
    stage: str,
) -> None:
    root = _root(tmp_path)
    release = _materialize(root / "candidate")
    config = prepare_candidate(
        root=root,
        release=release,
        source_env=None,
        docker_config=None,
        adoption_bundle=None,
    )

    def fail(point: str) -> None:
        if point == stage:
            raise FbctlError(f"injected {stage}")

    runner = FakeRunner()
    controller = ProductionController(runner=runner, promotion_hook=fail)
    controller._runtime_stopped = True  # noqa: SLF001 - exercises failure outcome at module seam

    with pytest.raises(FbctlError, match=f"injected {stage}"):
        controller._promote(config, [])  # noqa: SLF001 - exact promotion seam

    assert not (root / "runtime").exists()
    assert (root / "candidate").is_dir()
    assert not list(root.glob(".runtime-next-*"))
    assert not list(root.glob(".runtime-release-1-*"))
    controller._failure_cleanup(config)  # noqa: SLF001 - observable failure invariant
    assert any(step == "failure_cleanup" and "stop" in command for step, command in runner.commands)


def test_committed_pointer_survives_post_commit_process_death_and_reruns(tmp_path: Path) -> None:
    root = _root(tmp_path)
    runner = FakeRunner()

    def crash_after_commit(stage: str) -> None:
        if stage == "after_commit":
            raise SystemExit("simulated process death")

    crashing = ProductionController(
        runner=runner,
        probes=FakeProbes(),
        materialize=_materialize,
        sleep=lambda _seconds: None,
        promotion_hook=crash_after_commit,
    )
    with pytest.raises(SystemExit, match="simulated process death"):
        crashing.deploy(DeployOptions(root=root, rehearsal=True))

    first_payload = (root / "runtime").resolve()
    assert (root / "runtime").is_symlink()
    assert not (root / "candidate").exists()
    assert not any(step == "failure_cleanup" for step, _command in runner.commands)

    rerun = ProductionController(
        runner=FakeRunner(),
        probes=FakeProbes(),
        materialize=_materialize,
        sleep=lambda _seconds: None,
    )
    assert rerun.deploy(DeployOptions(root=root, rehearsal=True)).status == "READY"
    assert (root / "runtime").resolve() != first_payload
    assert not first_payload.exists()


def test_post_commit_cleanup_failure_never_invalidates_committed_runtime(tmp_path: Path) -> None:
    root = _root(tmp_path)
    initial = ProductionController(
        runner=FakeRunner(),
        probes=FakeProbes(),
        materialize=_materialize,
        sleep=lambda _seconds: None,
    )
    initial.deploy(DeployOptions(root=root, rehearsal=True))
    old_payload = (root / "runtime").resolve()

    def fail_previous_cleanup(stage: str) -> None:
        if stage == "cleanup_previous_runtime":
            raise FbctlError("injected cleanup failure")

    updated = ProductionController(
        runner=FakeRunner(),
        probes=FakeProbes(),
        materialize=_materialize,
        sleep=lambda _seconds: None,
        promotion_hook=fail_previous_cleanup,
    )
    assert updated.deploy(DeployOptions(root=root, rehearsal=True)).status == "READY"
    assert (root / "runtime").is_symlink()
    assert (root / "runtime").resolve() != old_payload
    assert old_payload.is_dir()


@pytest.mark.parametrize(
    "failure_stage",
    (
        "preflight",
        "pull",
        "resources",
        "infra",
        "migrate",
        "adoption",
        "runtime_config",
        "vision_config",
        "caddy",
        "consume_inputs",
    ),
)
def test_bootstrap_retry_reuses_durable_identity_after_mutation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    monkeypatch.setattr(os, "geteuid", lambda: 0)
    root = tmp_path / "fb-agent"
    _write_managed_vision_profile(root)
    (root / "shared").chmod(0o700)
    source = _source_env(tmp_path)
    with source.open("a", encoding="utf-8") as handle:
        handle.write(
            "VISION_X_TOKEN=vision-secret\n"
            "VISION_PROFILE_ID=profile-1\n"
            "PANEL_BASIC_AUTH_USER=owner\n"
            "PANEL_BASIC_AUTH_HASH=$2b$12$" + "a" * 53 + "\n"
        )
    source.chmod(0o600)
    monkeypatch.setattr("fbctl.controller.materialize_candidate", _materialize)
    monkeypatch.setattr(
        "fbctl.controller._normalize_profile_tree",
        lambda _path, *, uid, gid: None,
    )

    stages = {
        "_preflight": "preflight",
        "_pull": "pull",
        "_ensure_bootstrap_resources": "resources",
        "_start_infra": "infra",
        "_migrate": "migrate",
        "_bootstrap_adoption": "adoption",
        "_bootstrap_runtime_config": "runtime_config",
        "_bootstrap_vision_config": "vision_config",
    }

    should_fail = True

    def stage_action(name: str):
        def action(*_args, **_kwargs) -> None:
            if should_fail and name == failure_stage:
                raise FbctlError(f"injected bootstrap {name}")

        return action

    for method, stage in stages.items():
        monkeypatch.setattr(ProductionController, method, stage_action(stage))
    monkeypatch.setattr(fbctl_controller, "_provision_caddy", stage_action("caddy"))
    monkeypatch.setattr(
        fbctl_controller,
        "_consume_bootstrap_inputs",
        stage_action("consume_inputs"),
    )
    rehearsal = failure_stage not in {"caddy", "consume_inputs"}

    with pytest.raises(FbctlError, match=f"injected bootstrap {failure_stage}"):
        bootstrap_host(
            runner=FakeRunner(),
            root=root,
            source_env=source,
            adoption_bundle=None,
            desktop_profile_seed=None,
            docker_config=None,
            rehearsal=rehearsal,
        )

    persisted = parse_dotenv(root / "shared" / "source.env")
    durable = {
        key: persisted[key] for key in ("FB_AGENT_BOOTSTRAP_CLUSTER_ID", "POSTGRES_PASSWORD")
    }
    assert persisted["FB_AGENT_BOOTSTRAP_CLUSTER_ID"]
    assert persisted["POSTGRES_PASSWORD"]
    assert "VISION_X_TOKEN" not in persisted
    assert "PANEL_BASIC_AUTH_HASH" not in persisted

    should_fail = False
    bootstrap_host(
        runner=FakeRunner(),
        root=root,
        source_env=source,
        adoption_bundle=None,
        desktop_profile_seed=None,
        docker_config=None,
        rehearsal=rehearsal,
    )
    retried = parse_dotenv(root / "shared" / "source.env")
    assert retried == persisted
    assert durable["FB_AGENT_BOOTSTRAP_CLUSTER_ID"] == retried["FB_AGENT_BOOTSTRAP_CLUSTER_ID"]
    assert durable["POSTGRES_PASSWORD"] == retried["POSTGRES_PASSWORD"]


def test_restart_vision_recreates_browser_agent_network_namespace(tmp_path: Path) -> None:
    root = _root(tmp_path)
    runner = FakeRunner()
    controller = ProductionController(
        runner=runner,
        probes=FakeProbes(),
        materialize=_materialize,
        sleep=lambda _seconds: None,
    )
    controller.deploy(DeployOptions(root=root, rehearsal=True))
    runner.commands.clear()

    restart(root=root, service="vision-webtop", runner=runner, probes=FakeProbes())

    command = next(command for step, command in runner.commands if step == "restart")
    assert command[-2:] == ("vision-webtop", "browser-agent")


def test_missing_adoption_receipt_requires_bundle_without_host_marker(tmp_path: Path) -> None:
    root = _root(tmp_path)
    runner = FakeRunner(adoption_status=3)
    controller = ProductionController(
        runner=runner,
        probes=FakeProbes(),
        materialize=_materialize,
    )

    with pytest.raises(FbctlError, match="no adoption receipt"):
        controller.deploy(DeployOptions(root=root, rehearsal=True))

    assert not list((root / "shared").glob("*adoption*.sha256"))


def test_worker_heartbeat_must_be_fresh_and_exact() -> None:
    now = 1_000.0
    metrics = 'fb_agent_worker_heartbeat_timestamp_seconds{worker="observer"} 800\n'
    with pytest.raises(FbctlError, match="stale"):
        parse_worker_heartbeat(metrics, expected_worker="observer", now=now)
    with pytest.raises(FbctlError, match="missing"):
        parse_worker_heartbeat(metrics, expected_worker="autopause", now=now)
    poll = 'fb_agent_worker_db_poll_success_timestamp_seconds{worker="autopause"} 999\n'
    assert parse_worker_db_poll_success(poll, expected_worker="autopause", now=now).worker == (
        "autopause"
    )
    with pytest.raises(FbctlError, match="duplicated"):
        parse_worker_db_poll_success(poll + poll, expected_worker="autopause", now=now)


def test_public_smoke_never_sends_master_key_through_panel_auth_boundary(tmp_path: Path) -> None:
    root = _root(tmp_path)
    release = _materialize(root / "candidate")
    config = prepare_candidate(
        root=root,
        release=release,
        source_env=None,
        docker_config=None,
        adoption_bundle=None,
    )

    class RecordingProbes(FakeProbes):
        def __init__(self) -> None:
            self.json_urls: list[str] = []

        def json(self, url: str, *, headers=None, timeout: float = 15):
            self.json_urls.append(url)
            return super().json(url, headers=headers, timeout=timeout)

    probes = RecordingProbes()
    controller = ProductionController(runner=FakeRunner(), probes=probes)

    controller._public_smoke(config)  # noqa: SLF001
    assert not probes.json_urls


def test_publish_never_places_source_secret_in_ssh_arguments(tmp_path: Path) -> None:
    bundle = tmp_path / "release.pyz"
    build_bundle(
        source_root=ROOT,
        output=bundle,
        release_id="release-1",
        release_manifest=_manifest(tmp_path),
    )
    runner = FakeRunner()
    secret = b"API_KEY=top-secret-value\n"

    result = publish(
        host="root@example.test",
        bundle=bundle,
        root=Path("/opt/fb-agent"),
        source_env_stdin=True,
        docker_config=Path("/tmp/fb-agent-ghcr-1"),
        bootstrap=True,
        adoption_bundle_remote=Path("/opt/fb-agent/shared/adoption-bundle-v1.json"),
        desktop_profile_seed_remote=Path("/opt/fb-agent/shared/desktop-profile-seed"),
        enable_scanning=False,
        reuse_existing_caddy_credentials=True,
        project_known_legacy_source=True,
        migrate_existing_bootstrap_identity=True,
        runner=runner,
        source_stream=io.BytesIO(secret),
    )

    assert result["release_id"] == "release-1"
    rendered_commands = "\n".join(" ".join(command) for _step, command in runner.commands)
    assert "top-secret-value" not in rendered_commands
    assert "--reuse-existing-caddy-credentials" in rendered_commands
    assert "--project-known-legacy-source" in rendered_commands
    assert "--migrate-existing-bootstrap-identity" in rendered_commands
    assert "PANEL_BASIC_AUTH" not in rendered_commands
    assert "--source-env-stdin" in rendered_commands
    assert not any("source.env" in " ".join(command) for _step, command in runner.commands)
    bootstrap_call = next(
        command for step, command in runner.commands if step == "publish" and "bootstrap" in command
    )
    assert bootstrap_call[-1] == "--migrate-existing-bootstrap-identity"
    assert next(
        payload
        for step, command, payload in runner.stdin_payloads
        if step == "publish" and "bootstrap" in command
    ) == secret.decode("utf-8")
    assert all(
        payload is None
        for step, command, payload in runner.stdin_payloads
        if not (step == "publish" and "bootstrap" in command)
    )
    assert "sudo -n python3 -B" in rendered_commands
    assert "/opt/fb-agent/incoming" not in rendered_commands
    assert any(step == "publish_cleanup" for step, _command in runner.commands)


def test_routine_publish_uses_non_root_stage_and_sudo_without_source(tmp_path: Path) -> None:
    bundle = tmp_path / "release.pyz"
    build_bundle(
        source_root=ROOT,
        output=bundle,
        release_id="release-1",
        release_manifest=_manifest(tmp_path),
    )
    runner = FakeRunner()

    publish(
        host="deploy@example.test",
        bundle=bundle,
        root=Path("/opt/fb-agent"),
        source_env_stdin=False,
        docker_config=Path("/tmp/fb-agent-ghcr-1"),
        bootstrap=False,
        adoption_bundle_remote=None,
        desktop_profile_seed_remote=None,
        enable_scanning=False,
        runner=runner,
    )

    commands = [command for _step, command in runner.commands]
    assert any(command[:3] == ("ssh", "deploy@example.test", "mktemp") for command in commands)
    assert any("sudo" in command and "deploy" in command for command in commands)
    assert not any("source.env" in " ".join(command) for command in commands)
    assert not any("install" in command and "/opt/fb-agent" in command for command in commands)


def test_non_root_publish_cleans_remote_stage_after_deploy_failure(tmp_path: Path) -> None:
    bundle = tmp_path / "release.pyz"
    build_bundle(
        source_root=ROOT,
        output=bundle,
        release_id="release-1",
        release_manifest=_manifest(tmp_path),
    )
    runner = FakeRunner(fail_remote_deploy=True)

    with pytest.raises(FbctlError):
        publish(
            host="deploy@example.test",
            bundle=bundle,
            root=Path("/opt/fb-agent"),
            source_env_stdin=False,
            docker_config=Path("/tmp/fb-agent-ghcr-1"),
            bootstrap=False,
            adoption_bundle_remote=None,
            desktop_profile_seed_remote=None,
            enable_scanning=False,
            runner=runner,
        )

    cleanup_commands = [command for step, command in runner.commands if step == "publish_cleanup"]
    assert any("rm" in command for command in cleanup_commands)
    assert any("rmdir" in command for command in cleanup_commands)


def test_publish_rejects_root_copy_digest_mismatch_before_execution(tmp_path: Path) -> None:
    class BadDigestRunner(FakeRunner):
        def run(self, command, **kwargs):
            result = super().run(command, **kwargs)
            argv = tuple(os.fspath(part) for part in command)
            if "sha256sum" in argv:
                return CommandResult(0, f"{'0' * 64}  {argv[-1]}\n")
            return result

    bundle = tmp_path / "release.pyz"
    build_bundle(
        source_root=ROOT,
        output=bundle,
        release_id="release-1",
        release_manifest=_manifest(tmp_path),
    )
    runner = BadDigestRunner()

    with pytest.raises(FbctlError, match="integrity check failed"):
        publish(
            host="deploy@example.test",
            bundle=bundle,
            root=Path("/opt/fb-agent"),
            source_env_stdin=False,
            docker_config=None,
            bootstrap=False,
            adoption_bundle_remote=None,
            desktop_profile_seed_remote=None,
            enable_scanning=False,
            runner=runner,
        )

    assert not any("python3" in command for _step, command in runner.commands)
    assert [step for step, _command in runner.commands].count("publish_cleanup") == 4


@pytest.mark.parametrize(
    "root,docker_config,adoption,profile",
    [
        (Path("/opt/fb-agent;id"), None, None, None),
        (Path("/opt/fb-agent"), Path("/tmp/fb-agent-ghcr-1$(id)"), None, None),
        (Path("/opt/fb-agent"), None, Path("/opt/fb-agent/shared/../secrets"), None),
        (Path("/opt/fb-agent"), None, None, Path("/opt/fb-agent/shared/profile seed")),
    ],
)
def test_publish_rejects_unsafe_remote_paths_before_runner(
    tmp_path: Path,
    root: Path,
    docker_config: Path | None,
    adoption: Path | None,
    profile: Path | None,
) -> None:
    bundle = tmp_path / "release.pyz"
    build_bundle(
        source_root=ROOT,
        output=bundle,
        release_id="release-1",
        release_manifest=_manifest(tmp_path),
    )
    runner = FakeRunner()

    with pytest.raises(FbctlError, match="remote path|permitted remote scope"):
        publish(
            host="deploy@example.test",
            bundle=bundle,
            root=root,
            source_env_stdin=True,
            docker_config=docker_config,
            bootstrap=True,
            adoption_bundle_remote=adoption,
            desktop_profile_seed_remote=profile,
            enable_scanning=False,
            runner=runner,
            source_stream=io.BytesIO(b"API_KEY=top-secret-value\n"),
        )
    assert runner.commands == []


def test_doctor_and_status_accept_legacy_json_flag() -> None:
    parser = fbctl_main.build_parser()
    assert parser.parse_args(["doctor", "--json"]).json is True
    assert parser.parse_args(["status", "--json"]).json is True


class _VisionResult:
    def __init__(self, row):
        self.row = row

    def one_or_none(self):
        return self.row


class _VisionConnection:
    def __init__(self, row=None):
        self.row = row
        self.statements: list[str] = []
        self.parameters: list[dict[str, object] | None] = []

    async def execute(self, statement, parameters=None):
        rendered = str(statement)
        self.statements.append(rendered)
        self.parameters.append(parameters)
        if "SELECT x_token_encrypted" in rendered:
            return _VisionResult(self.row)
        return _VisionResult(None)


class _VisionEngine:
    def __init__(self, row=None):
        self.connection = _VisionConnection(row)

    @asynccontextmanager
    async def begin(self):
        yield self.connection


def _vision_bootstrap_module():
    path = ROOT / "scripts" / "bootstrap-vision-config.py"
    spec = importlib.util.spec_from_file_location("fbctl_test_vision_bootstrap", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_vision_bootstrap_creates_once_and_rejects_mismatch(monkeypatch) -> None:
    module = _vision_bootstrap_module()
    monkeypatch.setattr(module, "encrypt", lambda value: f"encrypted:{value}")
    monkeypatch.setattr(
        module,
        "decrypt",
        lambda value: value.removeprefix("encrypted:"),
    )
    created = _VisionEngine()
    outcome = __import__("asyncio").run(
        module.bootstrap_vision_config(
            created,
            x_token="token",
            profile_id="profile-1",
            folder_id="folder-1",
        )
    )
    assert outcome == "created"
    assert any("INSERT INTO vision_config" in sql for sql in created.connection.statements)
    assert created.connection.parameters[-1]["folder"] == "encrypted:folder-1"

    exact = _VisionEngine(("encrypted:token", "profile-1", "encrypted:folder-1"))
    assert (
        __import__("asyncio").run(
            module.bootstrap_vision_config(exact, x_token="token", profile_id="profile-1")
        )
        == "verified"
    )
    conflict = _VisionEngine(("encrypted:other", "profile-1", None))
    with pytest.raises(RuntimeError, match="conflicts"):
        __import__("asyncio").run(
            module.bootstrap_vision_config(conflict, x_token="token", profile_id="profile-1")
        )


def test_vision_bootstrap_imports_legacy_folder_only_when_db_is_empty(monkeypatch) -> None:
    module = _vision_bootstrap_module()
    monkeypatch.setattr(module, "encrypt", lambda value: f"encrypted:{value}")
    monkeypatch.setattr(module, "decrypt", lambda value: value.removeprefix("encrypted:"))

    legacy = _VisionEngine(("encrypted:token", "profile-1", None))
    outcome = __import__("asyncio").run(
        module.bootstrap_vision_config(
            legacy,
            x_token="token",
            profile_id="profile-1",
            folder_id="legacy-folder",
        )
    )

    assert outcome == "updated"
    assert "updated_at = GREATEST" in legacy.connection.statements[-1]
    assert legacy.connection.parameters[-1] == {"folder": "encrypted:legacy-folder"}


def test_nested_vision_profile_is_normalized_for_runtime_uid(tmp_path: Path, monkeypatch) -> None:
    profile = tmp_path / "vision-config"
    nested = profile / "browser" / "profile"
    nested.mkdir(parents=True, mode=0o700)
    _write(nested / "Preferences", "{}", 0o600)
    ownership: list[tuple[Path, int, int, bool]] = []
    monkeypatch.setattr(
        os,
        "chown",
        lambda path, uid, gid, *, follow_symlinks: ownership.append(
            (Path(path), uid, gid, follow_symlinks)
        ),
    )

    _normalize_profile_tree(profile, uid=1000, gid=1000)

    assert {item[0] for item in ownership} == {
        profile,
        profile / "browser",
        nested,
        nested / "Preferences",
    }
    assert all(item[1:] == (1000, 1000, False) for item in ownership)


def _snapshot_live_profile(profile: Path):
    return snapshot_profile_tree(
        profile,
        label="managed Vision configuration",
        required_uid=os.getuid(),
        required_gid=os.getgid(),
    )


def test_profile_survives_the_symlinks_xfce_creates_while_the_desktop_runs(
    tmp_path: Path,
) -> None:
    """Рабочий стол оставляет в профиле свои ссылки — это не повод падать.

    xfdesktop держит icons.screen.latest.rc ссылкой на файл текущего экрана.
    Пока проверка отвергала любую ссылку, bootstrap проходил ровно один раз —
    на нетронутом профиле, а после первого же запуска десктопа падал с
    «contains an unsafe entry».
    """
    profile = tmp_path / "vision-config"
    desktop = profile / ".config" / "xfce4" / "desktop"
    desktop.mkdir(parents=True, mode=0o700)
    profile.chmod(0o700)
    _write(profile / VISION_PROFILE_MARKER, VISION_PROFILE_MARKER_CONTENT)
    _write(desktop / "icons.screen.0.rc", "[Desktop]\n", 0o600)
    os.symlink("icons.screen.0.rc", desktop / "icons.screen.latest.rc")
    # Настоящий xfdesktop пишет цель в координатах контейнера, где профиль
    # смонтирован как /config: с точки зрения хоста путь абсолютный, но ведёт
    # внутрь того же профиля.
    os.symlink(
        "/config/.config/xfce4/desktop/icons.screen.0.rc",
        desktop / "icons.screen0-1350x725.rc",
    )

    receipt = _snapshot_live_profile(profile)

    assert receipt is not None
    links = sorted(entry.relative for entry in receipt.entries if entry.kind == "symlink")
    assert links == [
        (".config", "xfce4", "desktop", "icons.screen.latest.rc"),
        (".config", "xfce4", "desktop", "icons.screen0-1350x725.rc"),
    ]


@pytest.mark.parametrize(
    "target",
    [
        "/etc/shadow",
        "../../../../etc/shadow",
        "../../../..",
        # Похоже на путь контейнера, но выходит за профиль.
        "/config/../../etc/shadow",
        "/configuration/secret",
    ],
)
def test_profile_rejects_a_link_that_leaves_the_tree(tmp_path: Path, target: str) -> None:
    """Ссылка наружу остаётся небезопасной: цель читается, но не разыменовывается."""
    profile = tmp_path / "vision-config"
    nested = profile / ".config" / "xfce4"
    nested.mkdir(parents=True, mode=0o700)
    profile.chmod(0o700)
    _write(profile / VISION_PROFILE_MARKER, VISION_PROFILE_MARKER_CONTENT)
    os.symlink(target, nested / "escape.rc")

    with pytest.raises(FbctlError, match="unsafe link"):
        _snapshot_live_profile(profile)


def test_managed_port_owners_match_the_service_that_publishes_them() -> None:
    """Таблица владельцев портов обязана совпадать с ports в Compose.

    Гейт свободных портов считает «своим» только контейнер того сервиса,
    который здесь назван. Ошибка в имени превращает собственный контейнер
    в чужой и блокирует любой повторный деплой на живом хосте — при этом
    на глаз таблица выглядит правдоподобно.
    """
    compose_files = {
        "INFRA_PROJECT_NAME": ROOT / "deploy/compose/docker-compose.infra.yml",
        "APP_PROJECT_NAME": ROOT / "deploy/compose/docker-compose.app.yml",
        "DESKTOP_PROJECT_NAME": ROOT / "deploy/compose/docker-compose.desktop-agent.yml",
    }
    published: dict[str, tuple[str, str]] = {}
    for project_key, path in compose_files.items():
        service: str | None = None
        for raw in path.read_text(encoding="utf-8").splitlines():
            if re.fullmatch(r"  [A-Za-z0-9_-]+:", raw.rstrip()):
                service = raw.strip().rstrip(":")
                continue
            match = re.search(r"\$\{([A-Z_]+):", raw)
            if match and "127.0.0.1:" in raw and service is not None:
                published[match.group(1)] = (project_key, service)

    declared = {
        key: (project_key, service) for key, project_key, service in MANAGED_HOST_PORT_SERVICES
    }
    assert declared == published


class _LingeringSocketProbe:
    """Порт занят на первой пробе и свободен на следующих."""

    def __init__(self, port: int) -> None:
        self._port = port
        self.calls: list[tuple[str, int]] = []

    def __call__(self, host: str, port: int) -> bool:
        self.calls.append((host, port))
        return port == self._port and self.calls.count((host, port)) == 1


def test_preflight_waits_out_a_socket_the_kernel_has_not_released(tmp_path: Path) -> None:
    """Догорающий сокет — не чужой процесс.

    Сразу после остановки контейнера порт ещё занят, а владельца среди
    контейнеров уже нет. Без переспроса повторный деплой на живом хосте
    падал бы с обвинением в адрес несуществующего чужого процесса.
    """
    root = _root(tmp_path)
    config = prepare_candidate(
        root=root,
        release=_materialize(root / "candidate"),
        source_env=None,
        docker_config=None,
        adoption_bundle=None,
        rehearsal=True,
    )
    probe = _LingeringSocketProbe(int(config.values["POSTGRES_HOST_PORT"]))
    controller = ProductionController(
        runner=DockerPortInventoryRunner([]),
        sleep=lambda _seconds: None,
    )
    controller.port_probe = probe

    controller._require_available_infra_ports(values=config.values, docker_config=None)

    postgres_probe = ("127.0.0.1", int(config.values["POSTGRES_HOST_PORT"]))
    assert probe.calls.count(postgres_probe) > 1


def test_port_probe_asks_the_same_way_the_runtime_binds() -> None:
    """Проба обязана спрашивать порт так же, как его занимает Docker.

    Docker публикует порты с SO_REUSEADDR. Без него догорающий сокет в
    TIME_WAIT читается как занятый порт, хотя runtime занял бы его без
    помех: гейт оказывается строже реальности и валит деплой сразу после
    остановки контейнеров. Активный слушатель при этом обязан остаться
    виден — ради него гейт и существует.
    """
    scout = socket.socket()
    scout.bind(("127.0.0.1", 0))
    port = scout.getsockname()[1]
    scout.close()

    assert _tcp_port_is_occupied("127.0.0.1", port) is False

    listener = socket.socket()
    listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    listener.bind(("127.0.0.1", port))
    listener.listen(1)
    try:
        assert _tcp_port_is_occupied("127.0.0.1", port) is True
        client = socket.create_connection(("127.0.0.1", port))
        accepted, _peer = listener.accept()
        client.close()
        accepted.close()
    finally:
        listener.close()

    assert _tcp_port_is_occupied("127.0.0.1", port) is False


def test_rustdesk_channel_survives_the_source_schema(tmp_path) -> None:
    """Канал — единственный путь к столу, его переменные не вырезаются схемой.

    Пароль генерирует сам fbctl (как раньше пароль KasmVNC), адрес брокера
    имеет дефолт и переживает публикации как durable; ключ брокера в source
    не передаётся вовсе — стол читает его файлом из каталога брокера.
    Ретированные kasm-ключи отбрасываются молча: они ещё лежат в старых
    секретах, и валить publish из-за них незачем.
    """
    from fbctl.config import (
        DURABLE_KEYS,
        GENERATED_SECRETS,
        RETIRED_SOURCE_KEYS,
        SOURCE_ALLOWED_KEYS,
    )

    for key in ("DESKTOP_RUSTDESK_PASSWORD", "DESKTOP_RUSTDESK_SERVER", "DESKTOP_RUSTDESK_BIND"):
        assert key in SOURCE_ALLOWED_KEYS, f"{key} вырезается из source.env"
    assert "DESKTOP_RUSTDESK_KEY" not in SOURCE_ALLOWED_KEYS
    assert GENERATED_SECRETS["DESKTOP_RUSTDESK_PASSWORD"] == 32
    assert {"DESKTOP_RUSTDESK_SERVER", "DESKTOP_RUSTDESK_BIND"} <= DURABLE_KEYS
    assert RETIRED_SOURCE_KEYS == {
        "DESKTOP_KASM_SERVICE_USER",
        "DESKTOP_KASM_SERVICE_PASSWORD",
        "DESKTOP_RUSTDESK_KEY",
    }


def test_channel_address_defaults_to_a_public_name_not_a_bare_address() -> None:
    """Канал доступен без VPN, и адрес обязан быть именем, а не голым IP.

    Приватный адрес в дефолте означал бы стол, до которого нельзя дойти без
    VPN. Но и публичного IP мало: этой же строкой compose объявляет реле в
    своей сети, чтобы стол дошёл до него внутренним разрешением имени —
    путь контейнер → опубликованный порт хоста закрыт. IP сетевым алиасом не
    бывает, и с ним стол получал бы адрес, до которого не дотянуться, а
    сессия рвалась бы уже после успешного рукопожатия.
    """
    values = canonicalize_source(_minimal_source(), incumbent={})
    address = values["DESKTOP_RUSTDESK_SERVER"]

    assert address == "desktop.adpulse.su"
    with pytest.raises(ValueError):
        ipaddress.ip_address(address)


def test_channel_address_rejects_a_bare_ip() -> None:
    """Голый IP отсекается на входе, а не всплывает разрывом сессии в проде."""
    source = _minimal_source()
    source["DESKTOP_RUSTDESK_SERVER"] = "62.60.150.133"

    with pytest.raises(FbctlError, match="must be a DNS name"):
        canonicalize_source(source, incumbent={})


def test_channel_bind_default_matches_the_advertised_broker(tmp_path: Path) -> None:
    """Интерфейс брокера согласован с адресом, который объявляют оператору.

    Публичный адрес в паре с петлевым bind означал бы чистую установку,
    которая называет оператору адрес, на котором брокер не слушает: клиент
    RustDesk молча не соединился бы, а причина не видна ни в одном логе.
    """
    root = _root(tmp_path)
    config = prepare_candidate(
        root=root,
        release=_materialize(root / "candidate"),
        source_env=None,
        docker_config=None,
        adoption_bundle=None,
    )

    assert config.values["DESKTOP_RUSTDESK_BIND"] == "0.0.0.0"
    assert config.desktop_values["DESKTOP_RUSTDESK_SERVER"] == "desktop.adpulse.su"
    # Тем же именем compose подставляет алиас реле — без ключа в runtime env
    # подстановка развалилась бы уже на старте стола.
    assert config.values["DESKTOP_RUSTDESK_SERVER"] == "desktop.adpulse.su"


def test_desktop_environment_is_exactly_the_channel() -> None:
    """Стол получает ровно пароль и адрес брокера — и ничего сверх."""
    from fbctl.config import DESKTOP_ENV_KEYS, DESKTOP_ENV_REQUIRED_KEYS

    assert DESKTOP_ENV_REQUIRED_KEYS == (
        "DESKTOP_RUSTDESK_PASSWORD",
        "DESKTOP_RUSTDESK_SERVER",
    )
    assert DESKTOP_ENV_KEYS == DESKTOP_ENV_REQUIRED_KEYS
    assert not any(
        key.startswith(("POSTGRES", "TELEGRAM", "ANTHROPIC", "API_KEY")) for key in DESKTOP_ENV_KEYS
    )


class _ReadyzProbe:
    """Отдаёт ровно то, что вернул бы /system-readyz в проверяемом состоянии."""

    def __init__(self, payload: dict[str, object], status: int = 503) -> None:
        self.payload = payload
        self.status = status

    def json(self, url: str, *, headers=None, timeout: float = 15):
        del headers, timeout
        assert url.endswith("/system-readyz")
        return self.status, self.payload


def test_owner_paused_scanning_does_not_fail_the_release() -> None:
    """Выключенный скан — решение владельца, а не провал деплоя.

    Гейт готовности money-контура нужен, чтобы не промоутить сломанный
    рантайм. Сканирование к состоянию рантайма отношения не имеет: владелец
    вправе держать его выключенным и при этом обновлять приложение. Раньше
    релиз из-за этого не доходил до promote вообще.
    """
    probe = _ReadyzProbe(
        {
            "ready": False,
            "infrastructure_ready": True,
            "overall": "CRITICAL",
            "blockers": ["scanning_paused"],
            "degraded": [],
        }
    )

    assert require_system_ready(probe, "http://api") == ("scanning_paused",)


def test_real_money_defects_still_stop_the_release() -> None:
    """Всё, что не осознанная пауза, по-прежнему валит деплой поимённо."""
    probe = _ReadyzProbe(
        {
            "ready": False,
            "infrastructure_ready": True,
            "overall": "CRITICAL",
            "blockers": ["scanning_paused", "stale_money_tasks:3"],
            "degraded": [],
        }
    )

    with pytest.raises(FbctlError) as error:
        require_system_ready(probe, "http://api")

    assert "stale_money_tasks:3" in str(error.value)
    # Про паузу гейт не ругается: она не причина падения.
    assert "scanning_paused" not in str(error.value)


def test_broken_infrastructure_stops_the_release_even_without_blockers() -> None:
    probe = _ReadyzProbe(
        {
            "ready": False,
            "infrastructure_ready": False,
            "overall": "CRITICAL",
            "blockers": [],
            "degraded": [],
        }
    )

    with pytest.raises(FbctlError):
        require_system_ready(probe, "http://api")


def test_degraded_money_plane_stops_the_release() -> None:
    probe = _ReadyzProbe(
        {
            "ready": True,
            "infrastructure_ready": True,
            "overall": "DEGRADED",
            "blockers": [],
            "degraded": ["cabinet_actor_error:acc-1"],
        },
        status=200,
    )

    with pytest.raises(FbctlError) as error:
        require_system_ready(probe, "http://api")

    assert "cabinet_actor_error:acc-1" in str(error.value)


class _EnsureChannelProbe:
    """Считает вызовы ensure-cdp и отдаёт заранее заданные ответы."""

    def __init__(self, responses: list[dict[str, object]]) -> None:
        self.responses = responses
        self.calls: list[tuple[str, dict[str, str]]] = []

    def post_json(self, url, payload, *, headers=None, timeout: float = 15):
        self.calls.append((url, dict(headers or {})))
        return 200, self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]


def test_deploy_heals_the_desktop_channel_before_verifying_it() -> None:
    """Пересоздание стола гасит браузерный канал, и деплой обязан его поднять.

    Каждый деплой пересоздаёт контейнер стола, после чего Vision стартует без
    CDP-профиля. Раньше это лечили руками между шагами деплоя, иначе
    verify_application падал на несовпадении живого профиля с каноническим.
    """
    probe = _EnsureChannelProbe([{"ok": True, "status": "RECOVERED", "action": "restart"}])

    fbctl_probes.ensure_browser_channel(probe, "http://api", "k" * 24)

    url, headers = probe.calls[0]
    assert url == "http://api/api/vision/ensure-cdp"
    assert headers["X-API-Key"] == "k" * 24


def test_unhealed_channel_reports_the_reason_without_raising() -> None:
    """«Ещё не готов» — не отказ, а нормальное состояние после рестарта профиля.

    Пока это было исключением, вызывающий не мог отличить его от транспортной
    ошибки и повторял лечение в цикле ожидания — каждый повтор убивал браузер,
    который ещё поднимается (прод 18.08.2026). Причину проба возвращает, чтобы
    шаг положил её в свой отказ.
    """
    probe = _EnsureChannelProbe(
        [{"ok": False, "status": "UNAVAILABLE", "message": "Browser-agent profile recovery failed"}]
    )

    ready, reason = fbctl_probes.ensure_browser_channel(probe, "http://api", "k" * 24)

    assert ready is False
    assert reason == "Browser-agent profile recovery failed"
    assert len(probe.calls) == 1, "лечение обязано быть однократным"


def test_channel_healing_runs_before_the_application_gate() -> None:
    """Порядок важен: сначала поднять канал, потом проверять его гейтом."""
    steps = fbctl_controller.REHEARSAL_FAILPOINTS

    assert "ensure_desktop_channel" in steps
    assert steps.index("start_application") < steps.index("ensure_desktop_channel")
    assert steps.index("ensure_desktop_channel") < steps.index("verify_application")


def test_unavailable_healer_names_the_http_code() -> None:
    """Во время простоя код ответа — единственная диагностика у владельца.

    401 (протухший API_KEY), 404 (роутер не подключён) и 502 выглядели
    одинаково, хотя чинятся по-разному.
    """

    class _RejectingProbe:
        def post_json(self, url, payload, *, headers=None, timeout: float = 15):
            del url, payload, headers, timeout
            return 401, {"detail": "unauthorized"}

    with pytest.raises(FbctlError) as error:
        fbctl_probes.ensure_browser_channel(_RejectingProbe(), "http://api", "k" * 24)

    assert "401" in str(error.value)


class _DeadApiProbe(FakeProbes):
    """API не поднялся: любой HTTP-запрос к нему обрывается на соединении."""

    def __init__(self) -> None:
        self.post_calls: list[str] = []

    def status(self, url: str, *, timeout: float = 15) -> int:
        del timeout
        raise FbctlError(f"endpoint is unavailable: {url}")

    def post_json(self, url: str, payload, *, headers=None, timeout: float = 15):
        del payload, headers, timeout
        self.post_calls.append(url)
        raise FbctlError(f"endpoint is unavailable: {url}")


def test_channel_step_does_not_claim_a_dead_api_as_its_own_failure(tmp_path: Path) -> None:
    """Шаг стоит первым после старта API и обязан назвать чужой отказ чужим.

    Без собственной проверки доступности шаг три минуты крутил бы ожидание
    браузерного канала и завалил релиз сообщением про стол, хотя не поднялся
    сам API. Владелец получает ложный след в момент простоя единственного слота.
    """
    root = _root(tmp_path)
    config = prepare_candidate(
        root=root,
        release=_materialize(root / "candidate"),
        source_env=None,
        docker_config=None,
        adoption_bundle=None,
    )
    probes = _DeadApiProbe()
    slept: list[float] = []
    clock = iter(range(0, 100_000, 100))
    controller = ProductionController(
        runner=FakeRunner(),
        probes=probes,
        log=lambda _message: None,
        monotonic=lambda: float(next(clock)),
        sleep=slept.append,
    )

    with pytest.raises(FbctlError) as error:
        controller._ensure_desktop_channel(config)  # noqa: SLF001 - точный контракт шага

    message = str(error.value)
    assert "/healthz" in message
    assert "browser channel" not in message
    # Ни одной попытки лечить канал и ни одной секунды ожидания вслепую.
    assert probes.post_calls == []
    assert slept == []


class _UnhealableChannelProbes(FakeProbes):
    """API поднялся, но живой профиль — не тот, что назван в конфигурации."""

    def __init__(self) -> None:
        self.json_urls: list[str] = []

    def json(self, url: str, *, headers=None, timeout: float = 15):
        self.json_urls.append(url)
        if url.endswith("/api/settings/vision"):
            # Канал не встаёт и по чтению тоже — иначе это описание канала,
            # который на самом деле поднялся, просто ручка ответила устаревшим.
            return 200, {
                "required_browser_contract_version": 5,
                "browser_contract_version": 5,
                "browser_contract_compatible": True,
                "profile_id": "profile-1",
                "live_profile_id": None,
                "graph_probe_performed": False,
                "graph_probe_ok": False,
                "channel_status": "UNAVAILABLE",
                "browser_session_id": None,
            }
        return super().json(url, headers=headers, timeout=timeout)

    def post_json(self, url: str, payload, *, headers=None, timeout: float = 15):
        del url, payload, headers, timeout
        return 200, {
            "ok": False,
            "status": "UNAVAILABLE",
            "message": "Browser-agent profile recovery failed",
        }


def test_unhealable_channel_stops_the_whole_deploy_before_the_gate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Дефект выкатки не только падает сам — он обязан остановить весь релиз.

    Живой профиль, не совпавший с канонической конфигурацией, означает, что мы
    управляем не тем браузером: это свойство выкатки, а не состояние сессии
    Facebook. Такой отказ должен остановить деплой ДО гейта verify_application
    (иначе владелец получил бы вторичный диагноз вместо первичного), не
    промоутить кандидата и погасить money-воркеры через failure cleanup.
    """
    root = _root(tmp_path)
    runner = FakeRunner()
    probes = _UnhealableChannelProbes()
    clock = iter(range(0, 100_000, 100))
    monkeypatch.setattr(
        ProductionController,
        "_require_caddy_credentials",
        lambda *_args: None,
    )
    monkeypatch.setattr(ProductionController, "_sync_caddy", lambda *_args: None)
    monkeypatch.setattr(
        fbctl_controller,
        "_retire_legacy_systemd_units",
        lambda *_args, **_kwargs: [],
    )
    controller = ProductionController(
        runner=runner,
        probes=probes,
        materialize=_materialize,
        monotonic=lambda: float(next(clock)),
        sleep=lambda _seconds: None,
    )

    with pytest.raises(FbctlError, match="live Vision profile does not match"):
        controller.deploy(DeployOptions(root=root))

    # Гейт verify_application читает снимок оператора — до него не дошло.
    # Само /api/settings/vision шаг читает законно: это его проба ожидания.
    assert not any(url.endswith("/api/operator/snapshot") for url in probes.json_urls)
    assert not (root / "runtime").exists()
    assert any(step == "failure_cleanup" and "stop" in command for step, command in runner.commands)


# 18.08.2026 деплой упал с текстом «timed out waiting for recovered browser
# channel: browser channel is not ready (Profile restart completed but the
# channel is not ready)». Что делать оператору — в сообщении не было, и диагноз
# добывали вручную шестью ssh-запросами, пока money-воркеры лежали.
class _SignedOutChannelProbes(FakeProbes):
    """Образ выкачен верно, профиль тот самый, но Facebook разлогинен."""

    def json(self, url: str, *, headers=None, timeout: float = 15):
        if url.endswith("/api/settings/vision"):
            return 200, {
                "required_browser_contract_version": 5,
                "browser_contract_version": 5,
                "browser_contract_compatible": True,
                "profile_id": "profile-1",
                "live_profile_id": "profile-1",
                "graph_probe_performed": True,
                "graph_probe_ok": False,
                "channel_status": "UNAVAILABLE",
                "browser_session_id": None,
            }
        return super().json(url, headers=headers, timeout=timeout)

    def post_json(self, url: str, payload, *, headers=None, timeout: float = 15):
        del payload, headers, timeout
        assert url.endswith("/api/vision/ensure-cdp")
        return 200, {
            "ok": False,
            "status": "UNAVAILABLE",
            "message": "Profile restart completed but the channel is not ready",
        }


# Разлогин в Facebook — состояние браузера, а не свойство выкатки. Пока он валил
# релиз, каждый выход из аккаунта означал непромоутнутого кандидата и погашенные
# money-воркеры: прод останавливался ради того, чего он и так не сделал бы —
# очередь не отдаёт money-задачу без свежего browser_channel_readiness.
def test_signed_out_browser_channel_degrades_release_instead_of_failing_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    root = _root(tmp_path)
    runner = FakeRunner()
    clock = iter(range(0, 100_000, 100))
    log: list[str] = []
    monkeypatch.setattr(
        ProductionController,
        "_require_caddy_credentials",
        lambda *_args: None,
    )
    monkeypatch.setattr(ProductionController, "_sync_caddy", lambda *_args: None)
    monkeypatch.setattr(
        fbctl_controller,
        "_retire_legacy_systemd_units",
        lambda *_args, **_kwargs: [],
    )
    controller = ProductionController(
        runner=runner,
        probes=_SignedOutChannelProbes(),
        materialize=_materialize,
        monotonic=lambda: float(next(clock)),
        sleep=lambda _seconds: None,
        log=log.append,
    )

    result = controller.deploy(DeployOptions(root=root))

    assert result.status == "DEGRADED"
    assert "browser_channel_not_ready" in result.warnings
    # Релиз промоутнут, воркеры подняты: прод работает всем, что не требует
    # живого браузера.
    assert (root / "runtime").is_symlink()
    assert any(step == "start_workers" for step, _command in runner.commands)
    assert not any(step == "failure_cleanup" for step, _command in runner.commands)
    assert any("RustDesk" in message for message in log)


def test_desktop_channel_failure_names_the_operator_action() -> None:
    import inspect

    source = inspect.getsource(fbctl_controller.ProductionController._ensure_desktop_channel)
    assert "RustDesk" in source, "в отказе не назван канал доступа к столу"
    assert "профил" in source, "в отказе не сказано, что поднимать"


class _ColdDesktopProbes(FakeProbes):
    """Стол только что пересоздан: браузер профиля поднимается не мгновенно.

    Ручка ensure-cdp на неготовый канал делает ПРИНУДИТЕЛЬНЫЙ рестарт профиля.
    Пока она же служила пробой ожидания, шаг перезапускал браузер каждые пять
    секунд и убивал его раньше, чем тот успевал подняться (прод 18.08.2026:
    тридцать шесть рестартов за 180 секунд, chrome появился через 199 секунд
    после старта контейнера — на четыре секунды позже, чем шаг сдался).
    """

    def __init__(self, ready_after_reads: int = 4) -> None:
        self.heal_calls = 0
        self.vision_reads = 0
        self._ready_after_reads = ready_after_reads

    def post_json(self, url: str, payload, *, headers=None, timeout: float = 15):
        del payload, headers, timeout
        assert url.endswith("/api/vision/ensure-cdp")
        self.heal_calls += 1
        return 200, {
            "ok": False,
            "status": "UNAVAILABLE",
            "action": "restart",
            "message": "Profile restart completed but the channel is not ready",
        }

    def json(self, url: str, *, headers=None, timeout: float = 15):
        if url.endswith("/api/settings/vision"):
            self.vision_reads += 1
            if self.vision_reads < self._ready_after_reads:
                return 200, {
                    "required_browser_contract_version": 5,
                    "browser_contract_version": 5,
                    "browser_contract_compatible": True,
                    "profile_id": "profile-1",
                    "live_profile_id": None,
                    "graph_probe_performed": False,
                    "graph_probe_ok": False,
                    "channel_status": "UNAVAILABLE",
                    "browser_session_id": None,
                }
        return super().json(url, headers=headers, timeout=timeout)


def test_desktop_channel_heals_once_then_waits_by_reading() -> None:
    """Лечение — действие, а не проба: повторять его в цикле нельзя.

    Каждый повтор ensure-cdp перезапускает Vision-профиль, поэтому холодный
    старт, который длиннее интервала опроса, не может завершиться никогда —
    сколько бы времени шаг ни ждал.
    """
    probes = _ColdDesktopProbes()
    controller = ProductionController(
        runner=FakeRunner(),
        probes=probes,
        materialize=_materialize,
        monotonic=iter(range(0, 100_000, 5)).__next__,
        sleep=lambda _seconds: None,
    )
    config = SimpleNamespace(
        values={"APP_API_PORT": "8100", "APP_WEB_PORT": "8080", "APP_TMA_PORT": "8081"},
        api_key="k" * 24,
    )

    controller._ensure_desktop_channel(config)

    assert probes.heal_calls == 1, "профиль перезапускался повторно — браузер не успевает встать"
    assert probes.vision_reads >= 2, "готовность обязана проверяться чтением, а не лечением"


def test_bootstrap_projection_drops_retired_keys() -> None:
    """Ретированный ключ назван в отчёте, а не роняет bootstrap чистого host.

    Обычный deploy эти имена уже отбрасывает молча. Пока bootstrap на них падал,
    поднять чистый host из CI было нельзя вовсе.
    """
    from fbctl.config import RETIRED_SOURCE_KEYS, project_bootstrap_source

    source = {"API_KEY": "value", **{key: "legacy" for key in RETIRED_SOURCE_KEYS}}

    projected, dropped = project_bootstrap_source(source, project_known_legacy_source=False)

    assert set(projected) == {"API_KEY"}
    assert set(dropped) == set(RETIRED_SOURCE_KEYS)

    with pytest.raises(FbctlError):
        project_bootstrap_source(
            {"API_KEY": "value", "TOTALLY_UNKNOWN_KEY": "x"},
            project_known_legacy_source=True,
        )


def test_invalid_source_values_are_reported_together() -> None:
    """Негодные значения перечисляются одним сообщением, а не по одному за прогон.

    Проверка падала на первом же несоответствии, и несвежий source требовал
    столько кругов CI, сколько в нём устаревших значений. Значения при этом
    по-прежнему не попадают в текст — только имена и характер нарушения.
    """
    values = {
        "FB_AGENT_BOOTSTRAP_CLUSTER_ID": "0" * 32,
        "POSTGRES_PASSWORD": "x" * 20,
        "POSTGRES_USER": "fb_agent",
        "POSTGRES_DB": "fb_agent",
        "API_KEY": "sentinel-api-key",
        "ENCRYPTION_KEY": "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=",
        "TELEGRAM_OIDC_CLIENT_ID": "not-a-number",
        "TELEGRAM_OIDC_CLIENT_SECRET": "sentinel-secret",
        "DESKTOP_OWNER_TELEGRAM_USER_ID": "1",
        "DESKTOP_RUSTDESK_SERVER": "203.0.113.10",
    }

    with pytest.raises(FbctlError) as exc_info:
        validate_source_values(values)

    message = str(exc_info.value)
    assert "invalid API key" in message
    assert "Telegram OIDC client id must be numeric" in message
    assert "desktop channel address must be a DNS name" in message
    assert "sentinel-api-key" not in message
    assert "sentinel-secret" not in message
