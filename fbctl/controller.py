"""Deep single-slot deployment module behind the fbctl command interface."""

from __future__ import annotations

import base64
import fcntl
import grp
import json
import os
import pwd
import re
import secrets
import shutil
import stat
import sys
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Callable, Iterator, Sequence

from fbctl.bundle import materialize_candidate
from fbctl.config import (
    BOOTSTRAP_CADDY_KEYS,
    BOOTSTRAP_VISION_KEYS,
    RuntimeConfig,
    canonicalize_source,
    prepare_candidate,
    render_dotenv,
)
from fbctl.errors import FbctlError
from fbctl.files import (
    atomic_json,
    atomic_symlink,
    atomic_write,
    parse_dotenv,
    require_absolute_path,
    require_directory,
    require_private_file,
    sha256_file,
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
APP_SERVICES = frozenset({"api", "frontend", "mini-app", *WORKERS})
DESKTOP_SERVICES = frozenset({"vision-webtop", "browser-agent"})
RESTART_SERVICES = APP_SERVICES | DESKTOP_SERVICES
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
    "start_workers",
    "verify_worker_heartbeats",
    "verify_system_ready",
    "configure_telegram_webhook",
    "verify_telegram_webhook",
    # This is intentionally the final failure-class point.  The next action
    # commits an already-complete payload by switching the runtime pointer.
    "before_promote",
)


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
        if require_resources:
            self._require_managed_resources(config, include_campaign=True)

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
    ) -> None:
        inspected = self.runner.run(
            ("docker", kind, "inspect", "--format", "{{json .Labels}}", name),
            step="resource_inventory",
            env=environment,
            capture=True,
            check=False,
        )
        if inspected.returncode:
            raise FbctlError(f"managed Docker {kind} is missing: {name}")
        try:
            labels = json.loads(inspected.stdout)
        except (json.JSONDecodeError, TypeError) as exc:
            raise FbctlError(f"Docker {kind} has unreadable labels: {name}") from exc
        if not isinstance(labels, dict) or labels.get("com.fb-agent.managed") != "true":
            raise FbctlError(f"Docker {kind} is not managed by fbctl: {name}")
        if labels.get("com.fb-agent.cluster-id") != cluster_id:
            raise FbctlError(f"Docker {kind} belongs to another cluster: {name}")
        if labels.get("com.fb-agent.purpose") != purpose:
            raise FbctlError(f"Docker {kind} has the wrong purpose label: {name}")

    def _require_managed_resources(
        self,
        config: RuntimeConfig,
        *,
        include_campaign: bool,
    ) -> None:
        environment = self._environment(config)
        resources = [
            ("network", config.values["PLATFORM_NETWORK"], "platform"),
            ("volume", "fb_agent_safety_first_pgdata", "infra"),
            ("volume", "fb_agent_safety_first_redisdata", "infra"),
        ]
        if include_campaign:
            resources.append(("volume", "fb_agent_safety_first_campaign_uploads", "app"))
        for kind, name, purpose in resources:
            self._require_managed_resource(
                kind,
                name,
                config.values["FB_AGENT_BOOTSTRAP_CLUSTER_ID"],
                environment,
                purpose,
            )

    def _ensure_bootstrap_resources(self, config: RuntimeConfig) -> None:
        environment = self._environment(config)
        cluster_id = config.values["FB_AGENT_BOOTSTRAP_CLUSTER_ID"]
        resources = (
            ("network", config.values["PLATFORM_NETWORK"], "platform"),
            ("volume", "fb_agent_safety_first_pgdata", "infra"),
            ("volume", "fb_agent_safety_first_redisdata", "infra"),
            ("volume", "fb_agent_safety_first_campaign_uploads", "app"),
        )
        for kind, name, purpose in resources:
            inspected = self.runner.run(
                ("docker", kind, "inspect", name),
                step="bootstrap_resources",
                env=environment,
                check=False,
            )
            if inspected.returncode:
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
        lock_path = root / "shared" / "deploy.lock"
        descriptor = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.fchmod(descriptor, 0o600)
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
    source_env: Path,
    adoption_bundle: Path | None,
    desktop_profile_seed: Path | None,
    docker_config: Path | None,
    rehearsal: bool = False,
) -> dict[str, object]:
    if os.geteuid() != 0:
        raise FbctlError("bootstrap requires root privileges")
    if sys.version_info < (3, 12):
        raise FbctlError("Python 3.12 or newer is required")
    root = require_absolute_path(root, label="root")
    source_env = require_absolute_path(source_env, label="source environment")
    require_private_file(source_env)
    if adoption_bundle is not None:
        adoption_bundle = require_absolute_path(adoption_bundle, label="adoption bundle")
        if adoption_bundle.exists():
            require_private_file(adoption_bundle)
    if desktop_profile_seed is not None and desktop_profile_seed.exists():
        desktop_profile_seed = require_absolute_path(
            desktop_profile_seed,
            label="desktop profile seed",
        )
    for directory, mode in (
        (root, 0o755),
        (root / "shared", 0o700),
    ):
        directory.mkdir(parents=True, exist_ok=True, mode=mode)
        if directory.is_symlink():
            raise FbctlError(f"bootstrap directory must not be a symlink: {directory}")
        os.chmod(directory, mode)
    controller = ProductionController(runner=runner)
    candidate = root / "candidate"
    with controller._deployment_lock(root):  # noqa: SLF001 - same deep module
        shared = root / "shared"
        incumbent_path = shared / "source.env"
        incumbent = parse_dotenv(incumbent_path) if incumbent_path.is_file() else {}
        raw_source = parse_dotenv(source_env)
        caddy_bootstrap = {key: raw_source.get(key, "") for key in BOOTSTRAP_CADDY_KEYS}
        provision_caddy = not rehearsal
        _validate_bootstrap_transport(raw_source, provision_caddy=provision_caddy)
        source_values = canonicalize_source(raw_source, incumbent=incumbent)
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
        try:
            atomic_write(
                bootstrap_transport,
                render_dotenv(transport_values),
                mode=0o600,
            )
            if not vision_config.exists():
                if desktop_profile_seed is None:
                    raise FbctlError(
                        "Vision config is absent; first bootstrap requires --desktop-profile-seed"
                    )
                _copy_profile_seed(desktop_profile_seed, vision_config, uid=1000, gid=1000)
            else:
                require_directory(vision_config)
                _normalize_profile_tree(vision_config, uid=1000, gid=1000)
            release = materialize_candidate(candidate)
            release_id = str(release["release_id"])
            config = prepare_candidate(
                root=root,
                release=release,
                source_env=bootstrap_transport,
                docker_config=docker_config,
                adoption_bundle=(
                    adoption_bundle
                    if adoption_bundle is not None and adoption_bundle.exists()
                    else None
                ),
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
                adoption_bundle,
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
            _consume_bootstrap_inputs(
                adoption_bundle=adoption_bundle,
                desktop_profile_seed=desktop_profile_seed,
                canonical_profile=vision_config,
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
    }


def _copy_profile_seed(
    source: Path,
    destination: Path,
    *,
    uid: int,
    gid: int,
) -> None:
    require_directory(source, mode=0o700)
    marker = require_private_file(source / ".fb-agent-vision-profile-v1")
    if marker.read_bytes() != b"fb-agent-vision-profile-v1\n":
        raise FbctlError("desktop profile seed marker is invalid")
    for path in source.rglob("*"):
        metadata = path.lstat()
        if stat.S_ISLNK(metadata.st_mode) or not (
            stat.S_ISDIR(metadata.st_mode) or stat.S_ISREG(metadata.st_mode)
        ):
            raise FbctlError("desktop profile seed contains an unsafe entry")
        if stat.S_ISREG(metadata.st_mode) and metadata.st_nlink != 1:
            raise FbctlError("desktop profile seed contains a hard-linked file")
        if stat.S_IMODE(metadata.st_mode) & 0o022:
            raise FbctlError("desktop profile seed contains a writable shared entry")
    temporary = Path(tempfile.mkdtemp(prefix=".vision-config.", dir=destination.parent))
    try:
        shutil.copytree(source, temporary, dirs_exist_ok=True, symlinks=False)
        os.replace(temporary, destination)
        _normalize_profile_tree(destination, uid=uid, gid=gid)
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def _validate_bootstrap_transport(
    raw_source: dict[str, str],
    *,
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
    user = raw_source.get("PANEL_BASIC_AUTH_USER", "")
    password_hash = raw_source.get("PANEL_BASIC_AUTH_HASH", "")
    if not user or not password_hash:
        raise FbctlError("Caddy bootstrap credentials are missing from source environment")
    if not re.fullmatch(r"[A-Za-z0-9._@-]{1,64}", user) or not re.fullmatch(
        r"\$2[aby]\$[0-9]{2}\$[./A-Za-z0-9]{53}",
        password_hash,
    ):
        raise FbctlError("Caddy panel credentials are invalid")


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
    adoption_bundle: Path | None,
    desktop_profile_seed: Path | None,
    canonical_profile: Path,
) -> None:
    adoption: Path | None = None
    if adoption_bundle is not None and adoption_bundle.exists():
        adoption = require_absolute_path(adoption_bundle, label="adoption bundle")
        require_private_file(adoption)
    seed: Path | None = None
    if desktop_profile_seed is not None and desktop_profile_seed.exists():
        seed = require_absolute_path(desktop_profile_seed, label="desktop profile seed")
        if (
            seed == canonical_profile
            or seed in canonical_profile.parents
            or canonical_profile in seed.parents
        ):
            raise FbctlError("desktop profile seed overlaps canonical Vision configuration")
        require_directory(seed, mode=0o700)
        marker = require_private_file(seed / ".fb-agent-vision-profile-v1")
        if marker.read_bytes() != b"fb-agent-vision-profile-v1\n":
            raise FbctlError("desktop profile seed marker is invalid")
    if seed is not None:
        shutil.rmtree(seed)
        _fsync_parent(seed)
    if adoption is not None:
        adoption.unlink()
        _fsync_parent(adoption)


def _fsync_parent(path: Path) -> None:
    descriptor = os.open(path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
