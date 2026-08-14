"""Deep single-slot deployment module behind the fbctl command interface."""

from __future__ import annotations

import base64
import errno
import fcntl
import grp
import ipaddress
import json
import os
import pwd
import re
import secrets
import shutil
import socket
import stat
import sys
import time
from contextlib import contextmanager
from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterator, Mapping, Sequence

from fbctl.bundle import materialize_candidate
from fbctl.config import (
    APP_PROJECT_NAME,
    BOOTSTRAP_CADDY_KEYS,
    BOOTSTRAP_VISION_KEYS,
    DESKTOP_PROJECT_NAME,
    INFRA_PROJECT_NAME,
    MANAGED_HOST_PORTS,
    RuntimeConfig,
    canonicalize_source,
    parse_bootstrap_source_stdin,
    prepare_candidate,
    project_bootstrap_source,
    render_dotenv,
)
from fbctl.errors import FbctlError
from fbctl.files import (
    MAX_DOTENV_BYTES,
    PrivateFileSnapshot,
    atomic_json,
    atomic_symlink,
    atomic_write,
    parse_dotenv,
    require_absolute_path,
    require_directory,
    require_private_file,
    sha256_file,
    snapshot_private_file,
    trusted_shared_directory,
    unlink_unchanged_snapshot,
)
from fbctl.identity import (
    host_snapshot_is_current,
    remove_legacy_identity,
    resolve_bootstrap_identity,
    snapshot_host_identity,
)
from fbctl.probes import (
    ProbeClient,
    UrllibProbeClient,
    enable_observer_scanning,
    parse_worker_db_poll_success,
    parse_worker_heartbeat,
    require_exact_browser,
    require_ok_status,
    require_openapi,
    require_operator_snapshot,
    require_system_ready,
    require_telegram_webhook,
    wait_for,
)
from fbctl.runner import CommandResult, CommandRunner, SubprocessRunner, sealed_process_environment
from fbctl.vision_profile import (
    PRODUCTION_DESKTOP_PROFILE_SEED,
    PRODUCTION_ROOT,
    VISION_RUNTIME_GID,
    VISION_RUNTIME_UID,
    VisionProfileTreeReceipt,
    bootstrap_profile_is_current,
    copy_profile_from_receipt,
    remove_profile_tree_receipt,
    validate_bootstrap_vision_profile,
)

WORKERS: dict[str, str] = {
    "observer": "observer",
    "autopause_worker": "autopause",
    "meta_api": "meta_api",
    "telegram_delivery_worker": "telegram_delivery",
    "telegram_update_worker": "telegram_updates",
    "cleanup": "cleanup",
    "reconciler": "reconciler",
    "health_watchdog": "health_watchdog",
    "digest_scheduler": "digest_scheduler",
    "tracker_reconciliation_worker": "tracker_reconciliation_worker",
    "campaign_creator": "campaign_creator",
}
CADDY_ENV_PATH = Path("/etc/fb-agent/caddy.env")
APP_SERVICES = frozenset({"api", "frontend", "mini-app", *WORKERS})
DESKTOP_SERVICES = frozenset({"vision-webtop", "browser-agent"})
RESTART_SERVICES = APP_SERVICES | DESKTOP_SERVICES
MANAGED_VOLUME_RESOURCES = (
    ("volume", "fb_agent_infra_pgdata", "infra"),
    ("volume", "fb_agent_infra_redisdata", "infra"),
    ("volume", "fb_agent_app_campaign_uploads", "app"),
)
# Все published host-порты производственного контура и владеющий каждым портом
# сервис.  Проверять только infra было мало: коллизия по app/desktop всплывала
# на start_application, то есть уже после stop_runtime, и production оставался
# лежать.  Гейт обязан отвергнуть такой deploy до первой остановки.
MANAGED_HOST_PORT_SERVICES = (
    ("POSTGRES_HOST_PORT", "INFRA_PROJECT_NAME", "postgres"),
    ("REDIS_HOST_PORT", "INFRA_PROJECT_NAME", "redis"),
    ("APP_API_PORT", "APP_PROJECT_NAME", "api"),
    ("APP_WEB_PORT", "APP_PROJECT_NAME", "frontend"),
    ("APP_TMA_PORT", "APP_PROJECT_NAME", "mini-app"),
    ("BROWSER_GRPC_HOST_PORT", "DESKTOP_PROJECT_NAME", "browser-agent"),
    ("DESKTOP_HTTPS_PORT", "DESKTOP_PROJECT_NAME", "vision-webtop"),
)
# Ресурсы брошенного прежнего bootstrap. Мы их не трогаем: только сообщаем
# оператору, что они есть, чтобы он убрал их вручную после приёмки production.
LEGACY_DOCKER_RESOURCES = (
    ("network", "fb_agent_safety_first_platform"),
    ("volume", "fb_agent_safety_first_pgdata"),
    ("volume", "fb_agent_safety_first_redisdata"),
    ("volume", "fb_agent_safety_first_campaign_uploads"),
)
REHEARSAL_FAILPOINTS = (
    "preflight",
    "pull",
    "stop_runtime",
    "start_infra",
    "migrate",
    "verify_adoption",
    "start_desktop",
    "start_application",
    "verify_application",
    "enable_scanning",
    "configure_telegram_webhook",
    "verify_telegram_webhook",
    "start_workers",
    "verify_worker_heartbeats",
    "verify_system_ready",
    # This is intentionally the final failure-class point.  The next action
    # commits an already-complete payload by switching the runtime pointer.
    "before_promote",
)


def _tcp_port_is_occupied(host: str, port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as listener:
            listener.bind((host, port))
    except OSError as exc:
        if exc.errno == errno.EADDRINUSE:
            return True
        raise
    return False


def _docker_binding_intersects_host(binding_host: object, target_host: str) -> bool:
    if binding_host == "":
        return True
    if not isinstance(binding_host, str):
        return False
    try:
        binding_address = ipaddress.ip_address(binding_host)
        target_address = ipaddress.ip_address(target_host)
    except ValueError:
        return False
    if binding_address.version == target_address.version:
        return binding_address.is_unspecified or binding_address == target_address
    if isinstance(binding_address, ipaddress.IPv6Address) and isinstance(
        target_address, ipaddress.IPv4Address
    ):
        mapped_address = binding_address.ipv4_mapped
        return mapped_address is not None and (
            mapped_address.is_unspecified or mapped_address == target_address
        )
    return False


@dataclass(frozen=True)
class DeployOptions:
    root: Path
    docker_config: Path | None = None
    rehearsal: bool = False
    fail_after_step: str | None = None
    enable_scanning: bool = False


@dataclass
class DeployResult:
    release_id: str
    status: str
    warnings: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, object]:
        return {
            "release_id": self.release_id,
            "status": self.status,
            "warnings": list(self.warnings),
        }


class ProductionController:
    """Owns ordering, failure semantics and evidence for the production slot."""

    def __init__(
        self,
        *,
        runner: CommandRunner | None = None,
        probes: ProbeClient | None = None,
        log: Callable[[str], None] = lambda message: print(message, file=os.sys.stderr),
        now: Callable[[], float] = time.time,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        materialize: Callable[[Path], dict[str, object]] = materialize_candidate,
        prepare: Callable[..., RuntimeConfig] = prepare_candidate,
        promotion_hook: Callable[[str], None] = lambda _stage: None,
        port_probe: Callable[[str, int], bool] | None = None,
    ) -> None:
        self.runner = runner or SubprocessRunner()
        self.probes = probes or UrllibProbeClient()
        self.log = log
        self.now = now
        self.monotonic = monotonic
        self.sleep = sleep
        self.materialize = materialize
        self.prepare = prepare
        self._promotion_hook = promotion_hook
        self.port_probe = port_probe or _tcp_port_is_occupied
        self._runtime_stopped = False
        self._promoted = False
        self._completed_steps: list[str] = []

    def deploy(self, options: DeployOptions) -> DeployResult:
        if sys.version_info < (3, 12):
            raise FbctlError("Python 3.12 or newer is required")
        if options.fail_after_step and not options.rehearsal:
            raise FbctlError("deployment failpoints are available only in rehearsal mode")
        if (
            options.fail_after_step is not None
            and options.fail_after_step not in REHEARSAL_FAILPOINTS
        ):
            raise FbctlError("unsupported rehearsal failpoint")
        root = require_absolute_path(options.root, label="root")
        require_directory(root / "shared", mode=0o700)
        self._runtime_stopped = False
        self._promoted = False
        self._completed_steps = []
        with self._deployment_lock(root):
            release = self.materialize(root / "candidate")
            config = self.prepare(
                root=root,
                release=release,
                source_env=None,
                docker_config=options.docker_config,
                adoption_bundle=None,
                bootstrap=False,
                rehearsal=options.rehearsal,
            )
            warnings: list[str] = []
            try:
                self._step("preflight", options, lambda: self._preflight(config, options))
                self._step("pull", options, lambda: self._pull(config))
                self._step("stop_runtime", options, lambda: self._stop_runtime(config))
                self._step("start_infra", options, lambda: self._start_infra(config))
                self._step("migrate", options, lambda: self._migrate(config))
                self._step("verify_adoption", options, lambda: self._require_adoption(config))
                self._step("start_desktop", options, lambda: self._start_desktop(config))
                self._step("start_application", options, lambda: self._start_application(config))
                self._step(
                    "verify_application",
                    options,
                    lambda: self._verify_application(config),
                )
                if options.enable_scanning:
                    self._step(
                        "enable_scanning",
                        options,
                        lambda: enable_observer_scanning(
                            self.probes,
                            self._api_origin(config),
                            config.api_key,
                        ),
                    )
                self._step(
                    "configure_telegram_webhook",
                    options,
                    lambda: self._configure_webhook(config),
                )
                self._step(
                    "verify_telegram_webhook",
                    options,
                    lambda: self._verify_telegram(config),
                )
                self._step("start_workers", options, lambda: self._start_workers(config))
                self._step(
                    "verify_worker_heartbeats",
                    options,
                    lambda: self._verify_worker_heartbeats(config),
                )
                self._step(
                    "verify_system_ready",
                    options,
                    lambda: self._verify_system_ready(config),
                )
                if not options.rehearsal:
                    self._step("public_smoke", options, lambda: self._public_smoke(config))
                self._step("before_promote", options, lambda: None)
                self._promote(config, warnings)
            except BaseException:
                self._failure_cleanup(config)
                raise
            status = "DEGRADED" if warnings else "READY"
            return DeployResult(config.layout.release_id, status, warnings)

    def _step(self, name: str, options: DeployOptions, action: Callable[[], object]):
        self.log(f"[fbctl] step={name} started")
        try:
            result = action()
        except FbctlError as exc:
            if exc.step is None:
                exc.step = name
            self.log(f"[fbctl] step={name} failed: {exc}")
            raise
        except Exception as exc:
            wrapped = FbctlError(f"unexpected {type(exc).__name__}", step=name)
            self.log(f"[fbctl] step={name} failed: {wrapped}")
            raise wrapped from exc
        self._completed_steps.append(name)
        self.log(f"[fbctl] step={name} completed")
        if options.fail_after_step == name:
            raise FbctlError("rehearsal failpoint triggered", step=name)
        return result

    def _preflight(
        self,
        config: RuntimeConfig,
        options: DeployOptions,
        *,
        require_resources: bool = True,
        validate_caddy: bool = True,
    ) -> None:
        for plane in ("infra", "jobs", "desktop", "app"):
            path = config.compose_file(plane)
            if path.is_symlink() or not path.is_file():
                raise FbctlError(f"Compose file is missing or unsafe: {path}")
        environment = self._environment(config)
        self.runner.run(("docker", "compose", "version"), step="preflight", env=environment)
        self._report_legacy_resources(config)
        self._require_available_infra_ports(
            values=config.values,
            docker_config=config.docker_config,
        )
        for plane in ("infra", "jobs", "desktop"):
            self.runner.run(
                config.compose(plane, "config", "--quiet"),
                step="preflight",
                env=environment,
            )
        self.runner.run(
            config.compose("app", "--profile", "workers", "config", "--quiet"),
            step="preflight",
            env=environment,
        )
        if not options.rehearsal and validate_caddy:
            self._require_caddy_credentials(config)
            self.runner.run(
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
                step="preflight",
            )
        self._require_managed_resources(
            config,
            include_campaign=True,
            allow_missing=not require_resources,
        )

    def _require_caddy_credentials(self, config: RuntimeConfig) -> None:
        caddy_env = require_private_file(Path("/etc/fb-agent/caddy.env"))
        actual = parse_dotenv(
            caddy_env,
            required=(
                "PANEL_BASIC_AUTH_USER",
                "PANEL_BASIC_AUTH_HASH",
                "API_KEY",
                "DESKTOP_KASM_SERVICE_AUTH_B64",
            ),
        )
        expected = dict(actual)
        _sync_caddy_values({**config.app_values, **config.desktop_values}, expected)
        if expected != actual:
            raise FbctlError("Caddy credentials do not match canonical source configuration")

    def _pull(self, config: RuntimeConfig) -> None:
        environment = self._environment(config)
        for plane in ("infra", "jobs", "desktop"):
            self.runner.run(config.compose(plane, "pull"), step="pull", env=environment)
        self.runner.run(
            config.compose("app", "--profile", "workers", "pull"),
            step="pull",
            env=environment,
        )

    def _stop_runtime(self, config: RuntimeConfig) -> None:
        environment = self._environment(config)
        self.runner.run(
            config.compose(
                "app",
                "--profile",
                "workers",
                "down",
                "--remove-orphans",
                "--timeout",
                "90",
            ),
            step="stop_runtime",
            env=environment,
        )
        self.runner.run(
            config.compose("desktop", "down", "--remove-orphans", "--timeout", "90"),
            step="stop_runtime",
            env=environment,
        )
        self._runtime_stopped = True

    def _start_infra(self, config: RuntimeConfig) -> None:
        environment = self._environment(config)
        self.runner.run(
            config.compose(
                "infra",
                "up",
                "-d",
                "--remove-orphans",
                "--wait",
                "--wait-timeout",
                "240",
            ),
            step="start_infra",
            env=environment,
        )
        self._require_managed_resources(config, include_campaign=False)

    def _require_managed_resource(
        self,
        kind: str,
        name: str,
        cluster_id: str,
        environment: dict[str, str],
        purpose: str,
        *,
        allow_missing: bool = False,
    ) -> bool:
        inspected = self.runner.run(
            ("docker", kind, "inspect", "--format", "{{json .Labels}}", name),
            step="resource_inventory",
            env=environment,
            capture=True,
            check=False,
        )
        if inspected.returncode:
            if allow_missing:
                return False
            raise FbctlError(f"managed Docker {kind} is missing: {name}")
        resolution = "inspect and resolve this name collision manually before retrying"
        try:
            labels = json.loads(inspected.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise FbctlError(f"Docker {kind} has unreadable labels: {name}; {resolution}") from exc
        if not isinstance(labels, dict) or labels.get("com.fb-agent.managed") != "true":
            raise FbctlError(f"Docker {kind} is not managed by fbctl: {name}; {resolution}")
        if labels.get("com.fb-agent.cluster-id") != cluster_id:
            raise FbctlError(f"Docker {kind} belongs to another cluster: {name}; {resolution}")
        if labels.get("com.fb-agent.purpose") != purpose:
            raise FbctlError(f"Docker {kind} has the wrong purpose label: {name}; {resolution}")
        return True

    def _report_legacy_resources(self, config: RuntimeConfig) -> None:
        environment = self._environment(config)
        present: list[str] = []
        for kind, name in LEGACY_DOCKER_RESOURCES:
            inspected = self.runner.run(
                ("docker", kind, "inspect", name),
                step="resource_inventory",
                env=environment,
                capture=True,
                check=False,
            )
            if inspected.returncode == 0:
                present.append(f"{kind} {name}")
        if present:
            self.log(
                "[fbctl] info: legacy Docker resources detected and left untouched: "
                + ", ".join(present)
            )

    def _require_available_infra_ports(
        self,
        *,
        values: Mapping[str, str],
        docker_config: Path | None,
    ) -> None:
        required_ports = {
            values[key]: (key, values[project_key], service)
            for key, project_key, service in MANAGED_HOST_PORT_SERVICES
            if key in values and project_key in values
        }
        occupied_ports: dict[str, tuple[str, str, str]] = {}
        for host_port, required in required_ports.items():
            key, _project, _service = required
            try:
                port_number = int(host_port)
                occupied = self.port_probe("127.0.0.1", port_number)
            except (OSError, ValueError) as exc:
                raise FbctlError(
                    f"TCP host port probe failed for {key}={host_port} on 127.0.0.1"
                ) from exc
            if occupied:
                occupied_ports[host_port] = required

        environment = sealed_process_environment(docker_config=docker_config)
        listed = self.runner.run(
            ("docker", "container", "ls", "--all", "--quiet", "--no-trunc"),
            step="preflight",
            env=environment,
            capture=True,
        )
        container_ids = tuple(line.strip() for line in listed.stdout.splitlines() if line.strip())
        if any(
            re.fullmatch(r"[0-9a-f]{64}", container_id) is None for container_id in container_ids
        ):
            raise FbctlError("Docker returned an invalid container id")
        # Контейнер мог завершиться между `ls` и `inspect`: docker вернёт код 1,
        # напечатав корректный JSON по остальным. Если порт всё ещё занят, ниже
        # он останется без владельца и preflight завершится fail-closed.
        containers: object = []
        if container_ids:
            inspected = self.runner.run(
                ("docker", "container", "inspect", *container_ids),
                step="preflight",
                env=environment,
                capture=True,
                check=False,
            )
            if inspected.stdout.strip():
                try:
                    containers = json.loads(inspected.stdout)
                except (json.JSONDecodeError, TypeError) as exc:
                    raise FbctlError("Docker returned unreadable container inventory") from exc
        if not isinstance(containers, list):
            raise FbctlError("Docker returned invalid container inventory")
        if not occupied_ports:
            return

        conflicts: list[str] = []
        seen: set[tuple[str, str]] = set()
        attributed_ports: set[str] = set()
        for container in containers:
            if not isinstance(container, dict):
                raise FbctlError("Docker returned invalid container inventory")
            container_id = container.get("Id")
            raw_name = container.get("Name")
            if not isinstance(container_id, str) or not container_id:
                raise FbctlError("Docker returned a container without an id")
            name = raw_name.removeprefix("/") if isinstance(raw_name, str) else ""
            command_target = (
                name if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]+", name) else container_id
            )
            container_config = container.get("Config")
            network_settings = container.get("NetworkSettings")
            if not isinstance(container_config, dict) or not isinstance(network_settings, dict):
                raise FbctlError("Docker returned invalid container inventory")
            labels = container_config.get("Labels")
            network_ports = network_settings.get("Ports")
            if network_ports is None:
                network_ports = {}
            if not isinstance(network_ports, dict):
                raise FbctlError(f"Docker container has unreadable port bindings: {command_target}")
            binding_sources = [network_ports]
            host_config = container.get("HostConfig")
            if isinstance(host_config, dict):
                restart_policy = host_config.get("RestartPolicy")
                restart_name = (
                    restart_policy.get("Name") if isinstance(restart_policy, dict) else None
                )
                configured_ports = host_config.get("PortBindings")
                if restart_name not in {None, "", "no"}:
                    if configured_ports is None:
                        configured_ports = {}
                    if not isinstance(configured_ports, dict):
                        raise FbctlError(
                            f"Docker container has unreadable port bindings: {command_target}"
                        )
                    binding_sources.append(configured_ports)
            for ports in binding_sources:
                for container_port, bindings in ports.items():
                    if not isinstance(container_port, str):
                        continue
                    _private_port, separator, protocol = container_port.rpartition("/")
                    if separator != "/" or protocol != "tcp" or bindings is None:
                        continue
                    if not isinstance(bindings, list):
                        raise FbctlError(
                            f"Docker container has unreadable port bindings: {command_target}"
                        )
                    for binding in bindings:
                        if not isinstance(binding, dict):
                            raise FbctlError(
                                f"Docker container has unreadable port bindings: {command_target}"
                            )
                        host_port = binding.get("HostPort")
                        required = occupied_ports.get(host_port)
                        if required is None or not _docker_binding_intersects_host(
                            binding.get("HostIp"), "127.0.0.1"
                        ):
                            continue
                        attributed_ports.add(host_port)
                        key, project, service = required
                        # Свой же контур не считается коллизией: deploy сам остановит
                        # его на stop_runtime.  Совпадать обязан и cluster-id, иначе
                        # контейнер принадлежит другому (в том числе брошенному)
                        # контуру и должен быть остановлен оператором вручную.
                        is_current_runtime = (
                            isinstance(labels, dict)
                            and labels.get("com.fb-agent.managed") == "true"
                            and labels.get("com.fb-agent.cluster-id")
                            == values["FB_AGENT_BOOTSTRAP_CLUSTER_ID"]
                            and labels.get("com.docker.compose.project") == project
                            and labels.get("com.docker.compose.service") == service
                        )
                        if is_current_runtime or (key, container_id) in seen:
                            continue
                        seen.add((key, container_id))
                        display_name = name or container_id
                        conflicts.append(
                            f"{key}={host_port} is occupied by container "
                            f"{display_name} ({container_id[:12]}); stop it manually before "
                            f"retrying: sudo docker stop {command_target}"
                        )
        for host_port, (key, _project, _service) in occupied_ports.items():
            if host_port in attributed_ports:
                continue
            conflicts.append(
                f"{key}={host_port} is occupied, but Docker has no published TCP port owner; "
                "a process outside Docker or a host-network container is holding it; "
                "free the port manually before retrying"
            )
        if conflicts:
            raise FbctlError("Host TCP port collision: " + "; ".join(conflicts))

    @staticmethod
    def _managed_resources(
        config: RuntimeConfig,
        *,
        include_campaign: bool,
    ) -> tuple[tuple[str, str, str], ...]:
        resources = (
            ("network", config.values["PLATFORM_NETWORK"], "platform"),
            *MANAGED_VOLUME_RESOURCES[:2],
        )
        if include_campaign:
            return (*resources, MANAGED_VOLUME_RESOURCES[2])
        return resources

    def _require_managed_resources(
        self,
        config: RuntimeConfig,
        *,
        include_campaign: bool,
        allow_missing: bool = False,
    ) -> tuple[tuple[str, str, str], ...]:
        environment = self._environment(config)
        missing: list[tuple[str, str, str]] = []
        for kind, name, purpose in self._managed_resources(
            config,
            include_campaign=include_campaign,
        ):
            if not self._require_managed_resource(
                kind,
                name,
                config.values["FB_AGENT_BOOTSTRAP_CLUSTER_ID"],
                environment,
                purpose,
                allow_missing=allow_missing,
            ):
                missing.append((kind, name, purpose))
        return tuple(missing)

    def _ensure_bootstrap_resources(self, config: RuntimeConfig) -> None:
        environment = self._environment(config)
        cluster_id = config.values["FB_AGENT_BOOTSTRAP_CLUSTER_ID"]
        missing = self._require_managed_resources(
            config,
            include_campaign=True,
            allow_missing=True,
        )
        for kind, name, purpose in missing:
            self.runner.run(
                (
                    "docker",
                    kind,
                    "create",
                    "--label",
                    "com.fb-agent.managed=true",
                    "--label",
                    f"com.fb-agent.cluster-id={cluster_id}",
                    "--label",
                    f"com.fb-agent.purpose={purpose}",
                    name,
                ),
                step="bootstrap_resources",
                env=environment,
            )
        self._require_managed_resources(config, include_campaign=True)

    def _migrate(self, config: RuntimeConfig) -> None:
        self.runner.run(
            config.compose("jobs", "run", "--rm", "migrator"),
            step="migrate",
            env=self._environment(config),
        )

    def _adoption_status(self, config: RuntimeConfig) -> int:
        environment = self._environment(config)
        status = self.runner.run(
            config.compose("jobs", "run", "--rm", "adoption_status"),
            step="adoption",
            env=environment,
            check=False,
        )
        return status.returncode

    def _require_adoption(self, config: RuntimeConfig) -> None:
        status = self._adoption_status(config)
        if status == 0:
            return
        if status == 3:
            raise FbctlError("database has no adoption receipt; run fbctl bootstrap explicitly")
        raise FbctlError("adoption receipt status check failed")

    def _bootstrap_adoption(self, config: RuntimeConfig, adoption_bundle: Path | None) -> None:
        environment = self._environment(config)
        status = self._adoption_status(config)
        if status not in {0, 3}:
            raise FbctlError("adoption receipt status check failed")
        if status == 0:
            return
        if adoption_bundle is None or not adoption_bundle.is_file():
            raise FbctlError("clean database bootstrap requires an adoption bundle")
        require_private_file(adoption_bundle)
        self.runner.run(
            config.compose("jobs", "run", "--rm", "adoption_importer"),
            step="adoption",
            env=environment,
        )
        confirmed = self.runner.run(
            config.compose("jobs", "run", "--rm", "adoption_status"),
            step="adoption",
            env=environment,
            check=False,
        )
        if confirmed.returncode != 0:
            raise FbctlError("adoption importer did not commit a valid database receipt")

    def _bootstrap_runtime_config(self, config: RuntimeConfig) -> None:
        self.runner.run(
            config.compose("jobs", "run", "--rm", "runtime_config_bootstrap"),
            step="bootstrap_runtime_config",
            env=self._environment(config),
        )

    def _bootstrap_vision_config(self, config: RuntimeConfig) -> None:
        secret = Path(config.values["VISION_BOOTSTRAP_ENV_FILE"])
        if secret == Path("/dev/null"):
            raise FbctlError("Vision bootstrap secret transport is missing")
        require_private_file(secret)
        try:
            self.runner.run(
                config.compose("jobs", "run", "--rm", "vision_config_bootstrap"),
                step="bootstrap_vision_config",
                env=self._environment(config),
            )
        finally:
            secret.unlink(missing_ok=True)
            descriptor = os.open(secret.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)

    def _start_desktop(self, config: RuntimeConfig) -> None:
        self._publish_desktop_readiness(config)
        self.runner.run(
            config.compose(
                "desktop",
                "up",
                "-d",
                "--remove-orphans",
                "--wait",
                "--wait-timeout",
                "300",
            ),
            step="start_desktop",
            env=self._environment(config),
        )

    def _publish_desktop_readiness(self, config: RuntimeConfig) -> None:
        readiness = Path(config.values["DESKTOP_READINESS_DIR"])
        states = readiness / "states"
        readiness.mkdir(parents=True, exist_ok=True, mode=0o700)
        states.mkdir(parents=True, exist_ok=True, mode=0o700)
        require_directory(readiness, mode=0o700)
        require_directory(states, mode=0o700)
        user = config.desktop_values["DESKTOP_KASM_SERVICE_USER"]
        password = config.desktop_values["DESKTOP_KASM_SERVICE_PASSWORD"]
        if not user or ":" in user or len(password) < 32:
            raise FbctlError("desktop service credentials are invalid")
        state_id = f"{config.layout.release_id}-{sha256_file(config.layout.app_env)[:16]}"
        state = states / f"{state_id}.env"
        content = (
            f"DESKTOP_KASM_SERVICE_USER={user}\nDESKTOP_KASM_SERVICE_PASSWORD={password}\n"
        ).encode("utf-8")
        if state.exists():
            require_private_file(state)
            if state.read_bytes() != content:
                raise FbctlError("desktop readiness state conflicts with candidate credentials")
        else:
            atomic_write(state, content, mode=0o600)
        atomic_symlink(target=f"states/{state.name}", link=readiness / "active.env")

    def _start_application(self, config: RuntimeConfig) -> None:
        self.runner.run(
            config.compose(
                "app",
                "up",
                "-d",
                "--remove-orphans",
                "--wait",
                "--wait-timeout",
                "240",
                "api",
                "frontend",
                "mini-app",
            ),
            step="start_application",
            env=self._environment(config),
        )
        self._require_managed_resources(config, include_campaign=True)

    def _verify_application(self, config: RuntimeConfig) -> None:
        api = self._api_origin(config)
        require_ok_status(self.probes, f"{api}/healthz")
        require_ok_status(self.probes, f"{api}/readyz")
        require_openapi(self.probes, api)
        require_operator_snapshot(self.probes, api, config.api_key)
        wait_for(
            "exact Vision profile, browser contract v5 and Graph probe",
            lambda: require_exact_browser(self.probes, api, config.api_key),
            timeout=180,
            interval=3,
            monotonic=self.monotonic,
            sleep=self.sleep,
        )
        require_ok_status(
            self.probes,
            f"http://127.0.0.1:{config.values['APP_WEB_PORT']}/",
        )
        require_ok_status(
            self.probes,
            f"http://127.0.0.1:{config.values['APP_TMA_PORT']}/tma/",
        )

    def _configure_webhook(self, config: RuntimeConfig) -> CommandResult:
        return self.runner.run(
            config.compose("jobs", "run", "--rm", "telegram_webhook_configurator"),
            step="configure_telegram_webhook",
            env=self._environment(config),
        )

    def _start_workers(self, config: RuntimeConfig) -> None:
        self.runner.run(
            config.compose(
                "app",
                "--profile",
                "workers",
                "up",
                "-d",
                "--no-deps",
                "--wait",
                "--wait-timeout",
                "240",
                *WORKERS,
            ),
            step="start_workers",
            env=self._environment(config),
        )

    def _verify_worker_heartbeats(
        self,
        config: RuntimeConfig,
        services: Sequence[str] | None = None,
        *,
        timeout: float = 120,
    ) -> None:
        pending = {service: WORKERS[service] for service in tuple(services or WORKERS)}
        deadline = self.monotonic() + timeout
        last_errors: dict[str, str] = {}
        while pending:
            for service, expected_worker in tuple(pending.items()):
                try:
                    self._worker_heartbeat(
                        config,
                        service=service,
                        expected_worker=expected_worker,
                    )
                except FbctlError as exc:
                    last_errors[service] = str(exc)
                else:
                    pending.pop(service)
                    last_errors.pop(service, None)
            if not pending:
                return
            if self.monotonic() >= deadline:
                missing = ", ".join(
                    f"{service}: {last_errors.get(service, 'no evidence')}"
                    for service in sorted(pending)
                )
                raise FbctlError(f"worker heartbeat deadline exceeded ({missing})")
            self.sleep(2)

    def _worker_heartbeat(
        self,
        config: RuntimeConfig,
        *,
        service: str,
        expected_worker: str,
    ) -> None:
        script = (
            "import urllib.request;"
            "print(urllib.request.urlopen('http://127.0.0.1:9464/metrics',timeout=3)"
            ".read().decode('utf-8'))"
        )
        result = self.runner.run(
            config.compose(
                "app",
                "--profile",
                "workers",
                "exec",
                "-T",
                service,
                "python",
                "-c",
                script,
            ),
            step="verify_worker_heartbeats",
            env=self._environment(config),
            capture=True,
        )
        observed_now = self.now()
        parse_worker_heartbeat(
            result.stdout,
            expected_worker=expected_worker,
            now=observed_now,
        )
        if expected_worker in {"autopause", "meta_api"}:
            parse_worker_db_poll_success(
                result.stdout,
                expected_worker=expected_worker,
                now=observed_now,
            )

    def _verify_system_ready(self, config: RuntimeConfig) -> None:
        wait_for(
            "durable money control plane readiness",
            lambda: require_system_ready(self.probes, self._api_origin(config)),
            timeout=180,
            interval=3,
            monotonic=self.monotonic,
            sleep=self.sleep,
        )

    def _verify_telegram(self, config: RuntimeConfig) -> None:
        wait_for(
            "confirmed Telegram webhook",
            lambda: require_telegram_webhook(
                self.probes,
                self._api_origin(config),
                config.api_key,
            ),
            timeout=120,
            interval=3,
            monotonic=self.monotonic,
            sleep=self.sleep,
        )

    def _public_smoke(self, config: RuntimeConfig) -> None:
        wait_for(
            "public TLS, web, TMA and desktop auth routing",
            lambda: self._check_public_smoke(config),
            timeout=90,
            interval=3,
            monotonic=self.monotonic,
            sleep=self.sleep,
        )

    def _check_public_smoke(self, config: RuntimeConfig) -> None:
        require_ok_status(self.probes, f"{config.public_url}/healthz")
        require_ok_status(self.probes, f"{config.public_url}/readyz")
        for path in ("/", "/tma/"):
            status = self.probes.status(f"{config.public_url}{path}")
            if status < 200 or status >= 400:
                raise FbctlError(f"public surface returned HTTP {status}: {path}")
        desktop_status = self.probes.status("https://desktop.adpulse.su/")
        if desktop_status != 303:
            raise FbctlError("public desktop did not return the canonical unauthenticated redirect")

    def _promote(self, config: RuntimeConfig, warnings: list[str]) -> None:
        """Commit an already-probed candidate with one runtime-pointer rename.

        Containers are intentionally started from ``candidate`` while it is
        being verified.  Promotion copies that immutable candidate to a
        sibling payload, prepares every active-only file there, then swaps a
        relative ``runtime`` symlink with a single ``os.replace``.  Nothing
        after that commit can turn a healthy committed runtime into a failed
        deploy result.
        """
        root = config.layout.root
        candidate = config.layout.base
        runtime = root / "runtime"
        payload = self._new_runtime_payload(root, config.layout.release_id)
        next_pointer = root / f".runtime-next-{os.getpid()}-{secrets.token_hex(8)}"
        previous_payload = self._active_payload_if_present(root)
        final_values = {
            key: value.replace(os.fspath(candidate), os.fspath(payload))
            for key, value in config.values.items()
        }
        self._promotion_hook("copy_candidate")
        try:
            shutil.copytree(candidate, payload, symlinks=False)
            os.chmod(payload, 0o700)
            self._promotion_hook("write_runtime_env")
            atomic_write(payload / "runtime.env", render_dotenv(final_values), mode=0o400)
            self._promotion_hook("write_deploy_state")
            atomic_json(
                payload / "deploy-state.json",
                {
                    "schema": "fb-agent-deploy-state/v1",
                    "release_id": config.layout.release_id,
                    "status": "READY",
                    "warnings": list(warnings),
                    "completed_at": datetime.now(UTC).isoformat(),
                    "completed_steps": [*self._completed_steps, "promote"],
                },
            )
            self._promotion_hook("prepare_runtime_pointer")
            atomic_symlink(target=payload.name, link=next_pointer)
            self._promotion_hook("commit_runtime_pointer")
            # This is the sole active-runtime commit point.
            os.replace(next_pointer, runtime)
        except BaseException:
            next_pointer.unlink(missing_ok=True)
            if payload.exists() and not payload.is_symlink():
                shutil.rmtree(payload, ignore_errors=True)
            raise
        self._promoted = True

        for label, path in (
            ("candidate", candidate),
            ("previous_runtime", previous_payload),
        ):
            if path is None:
                continue
            try:
                self._promotion_hook(f"cleanup_{label}")
                if path.exists() and not path.is_symlink():
                    shutil.rmtree(path)
            except Exception as exc:  # post-commit cleanup is never a deploy failure
                self.log(f"[fbctl] committed runtime cleanup skipped for {label}: {exc}")

        # A process death after this hook is a committed runtime, not a
        # failure-class failpoint.  The next deploy is idempotent and starts
        # from the runtime pointer above.
        self._promotion_hook("after_commit")

    @staticmethod
    def _new_runtime_payload(root: Path, release_id: str) -> Path:
        for _attempt in range(16):
            payload = root / f".runtime-{release_id}-{secrets.token_hex(8)}"
            if not payload.exists() and not payload.is_symlink():
                return payload
        raise FbctlError("cannot allocate a unique runtime payload path")

    @staticmethod
    def _active_payload_if_present(root: Path) -> Path | None:
        runtime = root / "runtime"
        if not runtime.exists() and not runtime.is_symlink():
            return None
        try:
            metadata = runtime.lstat()
        except OSError as exc:
            raise FbctlError("active runtime path is unreadable") from exc
        if not stat.S_ISLNK(metadata.st_mode):
            raise FbctlError("active runtime must be an atomic fbctl pointer")
        target = Path(os.readlink(runtime))
        if (
            target.is_absolute()
            or target.parent != Path(".")
            or not target.name.startswith(".runtime-")
        ):
            raise FbctlError("active runtime pointer has an unsafe target")
        payload = root / target
        require_directory(payload, mode=0o700)
        return payload

    def _failure_cleanup(self, config: RuntimeConfig) -> None:
        if self._runtime_stopped and not self._promoted:
            self.runner.run(
                config.compose(
                    "app",
                    "--profile",
                    "workers",
                    "stop",
                    "--timeout",
                    "60",
                    *WORKERS,
                ),
                step="failure_cleanup",
                env=self._environment(config),
                check=False,
            )
            self.log(
                "[fbctl] candidate not promoted; money workers are stopped, "
                "available API/desktop containers were left for diagnostics"
            )

    def _environment(self, config: RuntimeConfig) -> dict[str, str]:
        return sealed_process_environment(docker_config=config.docker_config)

    @staticmethod
    def _api_origin(config: RuntimeConfig) -> str:
        return f"http://127.0.0.1:{config.values['APP_API_PORT']}"

    @contextmanager
    def _deployment_lock(self, root: Path) -> Iterator[None]:
        with self._deployment_lock_owned(root, required_uid=os.geteuid()):
            yield

    @contextmanager
    def _deployment_lock_owned(self, root: Path, *, required_uid: int) -> Iterator[None]:
        with trusted_shared_directory(root, required_uid=required_uid) as shared_fd:
            assert shared_fd is not None
            flags = (
                os.O_RDWR
                | getattr(os, "O_CLOEXEC", 0)
                | getattr(os, "O_NOFOLLOW", 0)
                | getattr(os, "O_NONBLOCK", 0)
            )
            created = False
            try:
                descriptor = os.open("deploy.lock", flags, dir_fd=shared_fd)
            except FileNotFoundError:
                try:
                    descriptor = os.open(
                        "deploy.lock",
                        flags | os.O_CREAT | os.O_EXCL,
                        0o600,
                        dir_fd=shared_fd,
                    )
                    created = True
                except FileExistsError:
                    try:
                        descriptor = os.open("deploy.lock", flags, dir_fd=shared_fd)
                    except OSError as exc:
                        raise FbctlError("deployment lock path is unsafe") from exc
                except OSError as exc:
                    raise FbctlError("deployment lock path is unsafe") from exc
            except OSError as exc:
                raise FbctlError("deployment lock path is unsafe") from exc
            try:
                if created:
                    try:
                        os.fchmod(descriptor, 0o600)
                    except OSError as exc:
                        raise FbctlError("deployment lock file is unsafe") from exc
                metadata = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(metadata.st_mode)
                    or metadata.st_uid != required_uid
                    or metadata.st_nlink != 1
                    or stat.S_IMODE(metadata.st_mode) != 0o600
                ):
                    raise FbctlError("deployment lock file is unsafe")
                try:
                    fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError as exc:
                    raise FbctlError("another fbctl deployment is running") from exc
                yield
            finally:
                os.close(descriptor)


def bootstrap_host(
    *,
    runner: CommandRunner,
    root: Path,
    source_env: Path | None,
    source_env_payload: bytes | None = None,
    adoption_bundle: Path | None,
    desktop_profile_seed: Path | None,
    docker_config: Path | None,
    rehearsal: bool = False,
    reuse_existing_caddy_credentials: bool = False,
    project_known_legacy_source: bool = False,
    migrate_existing_bootstrap_identity: bool = False,
) -> dict[str, object]:
    if os.geteuid() != 0:
        raise FbctlError("bootstrap requires root privileges")
    if sys.version_info < (3, 12):
        raise FbctlError("Python 3.12 or newer is required")
    if migrate_existing_bootstrap_identity and rehearsal:
        raise FbctlError("bootstrap identity migration is forbidden during rehearsal")
    root = require_absolute_path(root, label="root")
    if (source_env is None) == (source_env_payload is None):
        raise FbctlError("bootstrap requires exactly one source environment input")
    if source_env_payload is not None:
        raw_source_payload = source_env_payload
    else:
        assert source_env is not None
        source_env = require_absolute_path(source_env, label="source environment")
        shared = root / "shared"
        if source_env.parent == shared:
            with trusted_shared_directory(root, required_uid=os.getuid()) as shared_fd:
                assert shared_fd is not None
                source_snapshot = snapshot_private_file(
                    source_env,
                    label="bootstrap source environment",
                    maximum=MAX_DOTENV_BYTES,
                    required_uid=os.getuid(),
                    directory_fd=shared_fd,
                )
        else:
            source_snapshot = snapshot_private_file(
                source_env,
                label="bootstrap source environment",
                maximum=MAX_DOTENV_BYTES,
                required_uid=os.getuid(),
            )
        assert source_snapshot is not None
        raw_source_payload = source_snapshot.payload
    if adoption_bundle is not None:
        adoption_bundle = require_absolute_path(adoption_bundle, label="adoption bundle")
    if desktop_profile_seed is not None:
        desktop_profile_seed = require_absolute_path(
            desktop_profile_seed,
            label="desktop profile seed",
        )
    if (
        root == PRODUCTION_ROOT
        and desktop_profile_seed is not None
        and desktop_profile_seed != PRODUCTION_DESKTOP_PROFILE_SEED
    ):
        raise FbctlError("bootstrap requires the approved production desktop profile seed")
    raw_source, _ = project_bootstrap_source(
        parse_bootstrap_source_stdin(raw_source_payload),
        project_known_legacy_source=project_known_legacy_source,
    )
    host_identity = snapshot_host_identity(
        root,
        adoption_bundle,
        # ``sudo`` starts the production process with a real/effective uid of 0.
        # Using the real uid keeps the ownership invariant testable without
        # weakening the fixed uid=0 contract on the host.
        required_uid=os.getuid(),
        include_legacy=migrate_existing_bootstrap_identity,
    )
    identity = resolve_bootstrap_identity(
        explicit=raw_source,
        canonical=host_identity.canonical_values,
        legacy=host_identity.legacy_values,
        adoption_owner=(
            host_identity.adoption.owner_telegram_user_id if host_identity.adoption else None
        ),
        migration_enabled=migrate_existing_bootstrap_identity,
    )
    raw_source.update(identity.values)
    source_values = canonicalize_source(
        raw_source,
        incumbent=host_identity.canonical_values,
    )
    verified_adoption_bundle: bytes | None = None
    verified_adoption_source: PrivateFileSnapshot | None = None
    if host_identity.adoption is not None:
        assert adoption_bundle is not None
        verified_adoption_bundle = host_identity.adoption.payload
        verified_adoption_source = host_identity.adoption.snapshot
    provision_caddy = not rehearsal
    caddy_bootstrap = _resolve_caddy_bootstrap_credentials(
        raw_source,
        provision_caddy=provision_caddy,
        reuse_existing=reuse_existing_caddy_credentials,
    )
    _validate_bootstrap_transport(
        raw_source,
        caddy_bootstrap=caddy_bootstrap,
        provision_caddy=provision_caddy,
    )
    # This snapshot is before trusted_shared_directory(create=True), the
    # deployment lock, source.env, candidate/Docker/DB/Caddy mutation.
    vision_config = root / "shared" / "vision-config"
    profile_input = validate_bootstrap_vision_profile(
        canonical_profile=vision_config,
        desktop_profile_seed=desktop_profile_seed,
        seed_required_uid=os.getuid(),
        seed_required_gid=os.getgid(),
        canonical_required_uid=VISION_RUNTIME_UID,
        canonical_required_gid=VISION_RUNTIME_GID,
    )
    controller = ProductionController(runner=runner)
    with trusted_shared_directory(root, required_uid=os.getuid(), create=True) as shared_fd:
        assert shared_fd is not None
    candidate = root / "candidate"
    with controller._deployment_lock_owned(  # noqa: SLF001 - same deep module
        root,
        required_uid=os.getuid(),
    ):
        shared = root / "shared"
        incumbent_path = shared / "source.env"
        if not host_snapshot_is_current(
            host_identity,
            root=root,
        ):
            raise FbctlError("bootstrap identity sources changed after preflight")
        if not bootstrap_profile_is_current(profile_input):
            raise FbctlError("bootstrap Vision profile changed after preflight")
        # Занятый host-порт — единственная оставшаяся причина, по которой infra
        # не поднимется.  Проверяем здесь: identity и Vision profile уже
        # перепроверены под локом, а первой мутации ещё не было.
        controller._require_available_infra_ports(  # noqa: SLF001 - same deep module
            values={
                "FB_AGENT_BOOTSTRAP_CLUSTER_ID": source_values["FB_AGENT_BOOTSTRAP_CLUSTER_ID"],
                "INFRA_PROJECT_NAME": INFRA_PROJECT_NAME,
                "APP_PROJECT_NAME": APP_PROJECT_NAME,
                "DESKTOP_PROJECT_NAME": DESKTOP_PROJECT_NAME,
                **dict(MANAGED_HOST_PORTS),
            },
            docker_config=docker_config,
        )
        # Persist durable identity before the first Docker/DB mutation.  A
        # retry must reuse exactly these values even when the supplied source
        # omits generated fields.  Bootstrap-only Vision/Caddy plaintext was
        # removed by canonicalize_source and is never written here.
        atomic_write(
            incumbent_path,
            render_dotenv(source_values),
            mode=0o600,
        )
        bootstrap_transport = shared / f".bootstrap-source-{os.getpid()}.env"
        transport_values = dict(source_values)
        for key in (*BOOTSTRAP_CADDY_KEYS, "VISION_X_TOKEN", "VISION_PROFILE_ID"):
            if raw_source.get(key):
                transport_values[key] = raw_source[key]
        vision_config = shared / "vision-config"
        secret: Path | None = None
        release_id: str | None = None
        legacy_cleanup = "not_applicable"
        profile_seed_cleanup = "not_applicable"
        try:
            atomic_write(
                bootstrap_transport,
                render_dotenv(transport_values),
                mode=0o600,
            )
            if profile_input.seed_to_copy is not None:
                published_profile = copy_profile_from_receipt(
                    profile_input.active_receipt,
                    vision_config,
                    uid=VISION_RUNTIME_UID,
                    gid=VISION_RUNTIME_GID,
                )
                profile_input = replace(
                    profile_input,
                    canonical_receipt=published_profile,
                    active_receipt=published_profile,
                    seed_to_copy=None,
                )
            release = materialize_candidate(candidate)
            release_id = str(release["release_id"])
            adoption_snapshot: Path | None = None
            if verified_adoption_bundle is not None:
                adoption_snapshot = candidate / "secrets" / "adoption-bundle-v1.json"
                atomic_write(adoption_snapshot, verified_adoption_bundle, mode=0o600)
            config = prepare_candidate(
                root=root,
                release=release,
                source_env=bootstrap_transport,
                docker_config=docker_config,
                adoption_bundle=adoption_snapshot,
                bootstrap=True,
                rehearsal=rehearsal,
            )
            secret = Path(config.values["VISION_BOOTSTRAP_ENV_FILE"])
            bootstrap_options = DeployOptions(root=root, rehearsal=True)
            controller._preflight(  # noqa: SLF001 - same deep module
                config,
                bootstrap_options,
                require_resources=False,
                validate_caddy=False,
            )
            controller._pull(config)  # noqa: SLF001 - same deep module
            controller._ensure_bootstrap_resources(config)  # noqa: SLF001
            controller._start_infra(config)  # noqa: SLF001 - same deep module
            controller._migrate(config)  # noqa: SLF001 - same deep module
            controller._bootstrap_adoption(  # noqa: SLF001 - same deep module
                config,
                adoption_snapshot,
            )
            controller._bootstrap_runtime_config(config)  # noqa: SLF001
            controller._bootstrap_vision_config(config)  # noqa: SLF001
            if provision_caddy:
                _provision_caddy(
                    config.layout.base,
                    source_values,
                    caddy_bootstrap,
                    runner,
                )
            profile_seed_cleanup = _consume_bootstrap_inputs(
                adoption_bundle=verified_adoption_source,
                desktop_profile_seed=profile_input.seed_cleanup_receipt,
                canonical_profile=vision_config,
            )
            if identity.legacy_cleanup_eligible:
                legacy_cleanup = (
                    "removed" if remove_legacy_identity(host_identity) else "preserved_changed"
                )
        finally:
            if secret is not None:
                secret.unlink(missing_ok=True)
            bootstrap_transport.unlink(missing_ok=True)
            _fsync_parent(bootstrap_transport)
            if candidate.is_dir() and not candidate.is_symlink():
                shutil.rmtree(candidate)
                _fsync_parent(candidate)
        if release_id is None:  # pragma: no cover - guarded by the successful path above
            raise FbctlError("bootstrap release descriptor is missing")
    return {
        "status": "READY",
        "root": os.fspath(root),
        "release_id": release_id,
        "vision_config": os.fspath(vision_config),
        "caddy_provisioned": provision_caddy,
        "rehearsal": rehearsal,
        "legacy_identity_cleanup": legacy_cleanup,
        "profile_seed_cleanup": profile_seed_cleanup,
    }


def _validate_bootstrap_transport(
    raw_source: dict[str, str],
    *,
    caddy_bootstrap: dict[str, str],
    provision_caddy: bool,
) -> None:
    """Perform all bootstrap-only validation before durable state is written."""
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
    if not provision_caddy:
        return
    user = caddy_bootstrap["PANEL_BASIC_AUTH_USER"]
    password_hash = caddy_bootstrap["PANEL_BASIC_AUTH_HASH"]
    if not user or not password_hash:
        raise FbctlError("Caddy bootstrap credentials are missing from source environment")
    if not re.fullmatch(r"[A-Za-z0-9._@-]{1,64}", user) or not re.fullmatch(
        r"\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}",
        password_hash,
    ):
        raise FbctlError("Caddy panel credentials are invalid")


def _resolve_caddy_bootstrap_credentials(
    raw_source: dict[str, str],
    *,
    provision_caddy: bool,
    reuse_existing: bool,
) -> dict[str, str]:
    """Return only the panel pair; source input is never merged with Caddy env."""
    present = [key for key in BOOTSTRAP_CADDY_KEYS if key in raw_source]
    if len(present) == 1:
        raise FbctlError("Caddy bootstrap credentials must provide both panel keys or neither")
    if not provision_caddy:
        if reuse_existing:
            raise FbctlError("Caddy credential reuse is not available during rehearsal")
        return {key: raw_source.get(key, "") for key in BOOTSTRAP_CADDY_KEYS}
    if len(present) == 2:
        # The source environment is the explicit bootstrap authority.  The
        # reuse flag is only a fallback for an intentionally absent pair, so a
        # workflow can safely pass it for both supported source contracts.
        return {key: raw_source[key] for key in BOOTSTRAP_CADDY_KEYS}
    if not reuse_existing:
        raise FbctlError("Caddy bootstrap credentials are missing from source environment")
    caddy_env = require_private_file(CADDY_ENV_PATH)
    if caddy_env.stat().st_uid != 0:
        raise FbctlError("existing Caddy credentials must be owned by root")
    values = parse_dotenv(caddy_env)
    credentials = {key: values.get(key, "") for key in BOOTSTRAP_CADDY_KEYS}
    if not all(credentials.values()):
        raise FbctlError("existing Caddy credentials are missing panel keys")
    return credentials


def _normalize_profile_tree(root: Path, *, uid: int, gid: int) -> None:
    if not (1 <= uid <= 65_535 and 1 <= gid <= 65_535):
        raise FbctlError("Vision runtime UID/GID is invalid")
    require_directory(root)
    paths = [root, *root.rglob("*")]
    for path in paths:
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not (
            stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)
        ):
            raise FbctlError("canonical Vision profile contains an unsafe entry")
        if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
            raise FbctlError("canonical Vision profile contains a hard-linked file")
    for path in paths:
        os.chown(path, uid, gid, follow_symlinks=False)


def _sync_caddy_values(source: dict[str, str], target: dict[str, str]) -> None:
    user = source.get("DESKTOP_KASM_SERVICE_USER", "")
    password = source.get("DESKTOP_KASM_SERVICE_PASSWORD", "")
    api_key = source.get("API_KEY", "")
    if not user or ":" in user or len(password) < 32 or len(api_key) < 24:
        raise FbctlError("candidate credentials cannot be synchronized to Caddy")
    target["API_KEY"] = api_key
    target["DESKTOP_KASM_SERVICE_AUTH_B64"] = base64.b64encode(
        f"{user}:{password}".encode("utf-8")
    ).decode("ascii")


def _provision_caddy(
    resources: Path,
    source_values: dict[str, str],
    bootstrap_values: dict[str, str],
    runner: CommandRunner,
) -> None:
    if os.geteuid() != 0:
        raise FbctlError("Caddy provisioning must run as root")
    caddy_file = Path("/etc/caddy/Caddyfile")
    caddy_env = Path("/etc/fb-agent/caddy.env")
    panel_user = bootstrap_values.get("PANEL_BASIC_AUTH_USER", "")
    panel_hash = bootstrap_values.get("PANEL_BASIC_AUTH_HASH", "")
    if not re.fullmatch(r"[A-Za-z0-9._@-]{1,64}", panel_user) or not re.fullmatch(
        r"\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}", panel_hash
    ):
        raise FbctlError("Caddy panel credentials are invalid")
    if caddy_file.is_symlink() or (caddy_file.exists() and not caddy_file.is_file()):
        raise FbctlError("canonical Caddyfile is unsafe")
    import_line = "import /etc/caddy/sites-enabled/*.caddy"
    if caddy_file.is_file():
        caddy_content = caddy_file.read_text(encoding="utf-8")
        import_lines = [
            line.strip()
            for line in caddy_content.splitlines()
            if line.strip().startswith("import ") and "/etc/caddy/sites-enabled" in line
        ]
        if any(line != import_line for line in import_lines) or import_lines.count(import_line) > 1:
            raise FbctlError("Caddyfile contains a conflicting FB Agent site import")
        next_caddy_content = (
            caddy_content if import_lines else caddy_content.rstrip() + f"\n\n{import_line}\n"
        )
    else:
        next_caddy_content = f"{import_line}\n"
    try:
        caddy_identity = pwd.getpwnam("caddy")
        caddy_group = grp.getgrnam("caddy")
    except KeyError as exc:
        raise FbctlError("Caddy system user is missing") from exc
    target_values = {
        "PANEL_BASIC_AUTH_USER": panel_user,
        "PANEL_BASIC_AUTH_HASH": panel_hash,
    }
    _sync_caddy_values(source_values, target_values)
    caddy_env.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    atomic_write(caddy_env, render_dotenv(target_values), mode=0o600)
    sites = Path("/etc/caddy/sites-enabled")
    dropins = Path("/etc/systemd/system/caddy.service.d")
    log_dir = Path("/var/log/caddy")
    sites.mkdir(parents=True, exist_ok=True, mode=0o755)
    dropins.mkdir(parents=True, exist_ok=True, mode=0o755)
    log_dir.mkdir(parents=True, exist_ok=True, mode=0o755)
    for name in ("app.adpulse.su.caddy", "desktop.adpulse.su.caddy"):
        source = resources / "deploy" / "caddy" / name
        atomic_write(sites / name, source.read_bytes(), mode=0o644)
    atomic_write(
        dropins / "fb-agent-env.conf",
        (resources / "deploy/systemd/caddy-fb-agent-env.conf").read_bytes(),
        mode=0o644,
    )
    for name in ("fb-agent-access.log", "fb-agent-desktop-access.log"):
        path = log_dir / name
        if path.is_symlink():
            raise FbctlError(f"Caddy log path is unsafe: {path}")
        path.touch(mode=0o600, exist_ok=True)
        os.chmod(path, 0o600)
        os.chown(path, caddy_identity.pw_uid, caddy_group.gr_gid)
    if not caddy_file.is_file() or caddy_file.read_text(encoding="utf-8") != next_caddy_content:
        atomic_write(caddy_file, next_caddy_content.encode("utf-8"), mode=0o644)
    runner.run(("systemctl", "daemon-reload"), step="bootstrap")
    runner.run(
        (
            "caddy",
            "validate",
            "--config",
            caddy_file,
            "--adapter",
            "caddyfile",
            "--envfile",
            caddy_env,
        ),
        step="bootstrap",
    )
    runner.run(("systemctl", "reload", "caddy"), step="bootstrap")


def _consume_bootstrap_inputs(
    *,
    adoption_bundle: PrivateFileSnapshot | None,
    desktop_profile_seed: VisionProfileTreeReceipt | None,
    canonical_profile: Path,
) -> str:
    cleanup = "not_applicable"
    if desktop_profile_seed is not None:
        cleanup = (
            "removed"
            if remove_profile_tree_receipt(desktop_profile_seed)
            else "preserved_changed_or_quarantined"
        )
    if adoption_bundle is not None:
        shared = canonical_profile.parent
        if adoption_bundle.path.parent == shared:
            with trusted_shared_directory(
                shared.parent,
                required_uid=adoption_bundle.uid,
            ) as shared_fd:
                assert shared_fd is not None
                unlink_unchanged_snapshot(adoption_bundle, directory_fd=shared_fd)
        else:
            unlink_unchanged_snapshot(adoption_bundle)
    return cleanup


def _fsync_parent(path: Path) -> None:
    descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
