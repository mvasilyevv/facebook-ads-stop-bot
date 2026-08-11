from __future__ import annotations

import base64
import hashlib
import importlib.util
import io
import json
import os
import subprocess
import time
from contextlib import asynccontextmanager
from pathlib import Path

import pytest

from fbctl import __main__ as fbctl_main
from fbctl.bundle import BUNDLE_SCHEMA, IMAGE_KEYS, RESOURCE_FILES, build_bundle, inspect_bundle
from fbctl.config import canonicalize_source, load_active, prepare_candidate
from fbctl.controller import (
    REHEARSAL_FAILPOINTS,
    WORKERS,
    DeployOptions,
    ProductionController,
    _normalize_profile_tree,
    bootstrap_host,
)
from fbctl.errors import FbctlError
from fbctl.files import parse_dotenv
from fbctl.operations import doctor, restart
from fbctl.probes import parse_worker_db_poll_success, parse_worker_heartbeat
from fbctl.publish import publish
from fbctl.runner import CommandResult

ROOT = Path(__file__).resolve().parents[2]
IMAGE = "registry.example/fb-agent@sha256:" + "a" * 64


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


def _source_env(tmp_path: Path) -> Path:
    values = {
        "ENCRYPTION_KEY": base64.urlsafe_b64encode(b"e" * 32).decode("ascii"),
        "ENCRYPTION_KEY_VERIFY": "verification-value",
        "TELEGRAM_BOT_TOKEN": "123456:test-token",
        "TELEGRAM_OIDC_CLIENT_ID": "123456",
        "TELEGRAM_OIDC_CLIENT_SECRET": "o" * 40,
        "TELEGRAM_OIDC_REDIRECT_URI": "https://app.adpulse.su/auth/telegram/callback",
        "API_KEY": "k" * 32,
        "DESKTOP_OWNER_TELEGRAM_USER_ID": "123456",
    }
    return _write(
        tmp_path / "source.env",
        "".join(f"{key}={value}\n" for key, value in values.items()),
    )


def _root(tmp_path: Path) -> Path:
    root = tmp_path / "fb-agent"
    (root / "shared" / "vision-config").mkdir(parents=True)
    (root / "shared").chmod(0o700)
    _write(root / "shared" / "source.env", _source_env(tmp_path).read_bytes())
    return root


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
        self.adoption_status = adoption_status
        self.now = now or time.time()
        self.cluster_id = ""
        self.fail_remote_deploy = fail_remote_deploy

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
        del env, capture, input_text, timeout
        argv = tuple(os.fspath(part) for part in command)
        self.commands.append((step, argv))
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
        elif argv[:3] == ("ssh", argv[1], "mktemp"):
            result = CommandResult(0, "/tmp/fbctl-release-1-AbCd1234\n")
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
                result = CommandResult(
                    0,
                    json.dumps(
                        {
                            "com.fb-agent.managed": "true",
                            "com.fb-agent.cluster-id": self.cluster_id,
                            "com.fb-agent.purpose": (
                                "platform"
                                if argv[-1] == "fb_agent_safety_first_platform"
                                else "app"
                                if argv[-1] == "fb_agent_safety_first_campaign_uploads"
                                else "infra"
                            ),
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
            return 200, {"ready": True, "overall": "HEALTHY", "blockers": []}
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
        "DESKTOP_KASM_SERVICE_USER",
        "DESKTOP_KASM_SERVICE_PASSWORD",
    }
    for forbidden in (
        "TELEGRAM_BOT_TOKEN",
        "POSTGRES_PASSWORD",
        "API_KEY",
        "ENCRYPTION_KEY",
        "OPENAI_API_KEY",
        "ANTHROPIC_API_KEY",
    ):
        assert forbidden not in values
    app_values = config.layout.app_env.read_text(encoding="utf-8")
    assert "DESKTOP_KASM_SERVICE_USER=adpulse-desktop" in app_values
    assert "DESKTOP_KASM_SERVICE_PASSWORD=" in app_values


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
    root = tmp_path / "fb-agent"
    vision_config = root / "shared" / "vision-config"
    vision_config.mkdir(parents=True)
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


def test_invalid_bootstrap_only_transport_never_persists_canonical_source(tmp_path: Path) -> None:
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


def test_deploy_promotes_only_one_runtime_after_all_evidence(tmp_path: Path) -> None:
    root = _root(tmp_path)
    canonical_source = (root / "shared" / "source.env").read_bytes()
    now = time.time()
    runner = FakeRunner(now=now)
    controller = ProductionController(
        runner=runner,
        probes=FakeProbes(),
        materialize=_materialize,
        now=lambda: now,
        sleep=lambda _seconds: None,
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
    steps = [step for step, _command in runner.commands]
    assert steps.index("start_workers") < steps.index("configure_telegram_webhook")


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


@pytest.mark.parametrize("failure_stage", ("resources", "migrate", "adoption"))
def test_bootstrap_retry_reuses_durable_identity_after_mutation_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    failure_stage: str,
) -> None:
    root = tmp_path / "fb-agent"
    vision_config = root / "shared" / "vision-config"
    vision_config.mkdir(parents=True)
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

    with pytest.raises(FbctlError, match=f"injected bootstrap {failure_stage}"):
        bootstrap_host(
            runner=FakeRunner(),
            root=root,
            source_env=source,
            adoption_bundle=None,
            desktop_profile_seed=None,
            docker_config=None,
            rehearsal=True,
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
        rehearsal=True,
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
        desktop_profile_seed_remote=Path("/opt/fb-agent/shared/vision-profile-seed"),
        enable_scanning=False,
        runner=runner,
        source_stream=io.BytesIO(secret),
    )

    assert result["release_id"] == "release-1"
    rendered_commands = "\n".join(" ".join(command) for _step, command in runner.commands)
    assert "top-secret-value" not in rendered_commands
    assert "--source-env" in rendered_commands
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

    async def execute(self, statement, parameters=None):
        del parameters
        rendered = str(statement)
        self.statements.append(rendered)
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
        module.bootstrap_vision_config(created, x_token="token", profile_id="profile-1")
    )
    assert outcome == "created"
    assert any("INSERT INTO vision_config" in sql for sql in created.connection.statements)

    exact = _VisionEngine(("encrypted:token", "profile-1"))
    assert (
        __import__("asyncio").run(
            module.bootstrap_vision_config(exact, x_token="token", profile_id="profile-1")
        )
        == "verified"
    )
    conflict = _VisionEngine(("encrypted:other", "profile-1"))
    with pytest.raises(RuntimeError, match="conflicts"):
        __import__("asyncio").run(
            module.bootstrap_vision_config(conflict, x_token="token", profile_id="profile-1")
        )


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
