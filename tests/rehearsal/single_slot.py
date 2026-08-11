"""Exercise the shipped fbctl zipapp against real Docker and PostgreSQL.

Only Meta/Vision and Telegram network edges are deterministic stubs. The
control bundle, Compose files, migrations, database, API and all workers are
the exact release artifacts produced by the image workflow.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from apps.cleanup_worker.retention import get_default_policy

REGISTRY_IMAGE = (
    "registry:2@sha256:a3d8aaa63ed8681a604f1dea0aa03f100d5895b6a58ace528858a7b332415373"
)
IMMUTABLE_IMAGE = re.compile(r"^\S+@sha256:[0-9a-f]{64}$")
PLATFORM_NETWORK = "fb_agent_safety_first_platform"
MANAGED_VOLUMES = (
    "fb_agent_safety_first_campaign_uploads",
    "fb_agent_safety_first_pgdata",
    "fb_agent_safety_first_redisdata",
)
WORKER_SERVICES = frozenset(
    {
        "observer",
        "autopause_worker",
        "meta_api",
        "telegram_delivery_worker",
        "telegram_update_worker",
        "cleanup",
        "reconciler",
        "health_watchdog",
        "digest_scheduler",
        "tracker_reconciliation_worker",
        "campaign_creator",
    }
)
TELEGRAM_WEBHOOK_SECRET = "rehearsal_webhook_secret_0123456789abcdef"
ALERTMANAGER_WEBHOOK_SECRET = "rehearsal_alertmanager_secret_0123456789abcdef"
API_KEY = "rehearsal_api_key_0123456789abcdef"
SCENARIOS = ("full", "failpoints", "acceptance")


class RehearsalError(RuntimeError):
    """The release failed an executable deployment invariant."""


def _run(
    arguments: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
    capture: bool = False,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        arguments,
        cwd=cwd,
        check=check,
        text=True,
        capture_output=capture,
        env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
    )


def _require_ci_acknowledgement() -> None:
    if os.geteuid() != 0:
        raise RehearsalError("single-slot rehearsal requires root privileges")
    if os.environ.get("GITHUB_ACTIONS") != "true":
        raise RehearsalError("single-slot rehearsal is restricted to an ephemeral Actions host")
    if os.environ.get("FB_AGENT_REHEARSAL_ACK") != "single-slot":
        raise RehearsalError("set FB_AGENT_REHEARSAL_ACK=single-slot explicitly")
    raw_docker_config = os.environ.get("DOCKER_CONFIG", "")
    docker_config = Path(raw_docker_config)
    if not raw_docker_config or not docker_config.is_absolute():
        raise RehearsalError("DOCKER_CONFIG must be an absolute path")
    if not docker_config.is_dir() or not (docker_config / "config.json").is_file():
        raise RehearsalError("DOCKER_CONFIG must contain an existing config.json")


def _load_release(path: Path) -> dict[str, Any]:
    try:
        release = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise RehearsalError("release.json is unreadable") from exc
    if set(release) != {"schema", "release_id", "images"}:
        raise RehearsalError("release.json has unexpected top-level fields")
    images = release.get("images")
    if release.get("schema") != "fb-agent-release/v1" or not isinstance(images, dict):
        raise RehearsalError("release.json has an unsupported contract")
    if not images or any(
        not isinstance(value, str) or not IMMUTABLE_IMAGE.fullmatch(value)
        for value in images.values()
    ):
        raise RehearsalError("release.json contains a mutable image")
    return release


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def build_adoption_bundle() -> dict[str, Any]:
    """Return the smallest monitored USD cabinet accepted by adoption-bundle/v1."""
    sections: dict[str, Any] = {
        "accounts": [{"account_id": "111"}],
        "offers": [
            {
                "code": "REHEARSAL",
                "name": "Rehearsal USD",
                "vertical": "iGaming",
                "pixel_id": None,
                "is_active": True,
                "account_ids": ["111"],
                "countries": ["GH"],
            }
        ],
        "offer_rules": [
            {
                "offer_code": "REHEARSAL",
                "cpa_threshold": "3",
                "currency": "USD",
                "frequency_threshold": None,
                "stop_percent_of_rule": "80",
                "warning_percent_of_stop": "80",
            }
        ],
        "observer_settings": {
            "interval_seconds": 30,
            "owner_campaign_tag": None,
            "campaign_ids": ["9001"],
        },
        "operator_display_settings": {"timezone_name": "Europe/Kaliningrad"},
        "recipients": [
            {
                "chat_id": 1,
                "telegram_user_id": 1,
                "username": "rehearsal_owner",
                "display_name": "Rehearsal Owner",
                "role": "owner",
            }
        ],
        "recipient_preferences": [
            {
                "telegram_user_id": 1,
                "timezone": "Europe/Kaliningrad",
                "min_severity": "warning",
                "quiet_hours_start": None,
                "quiet_hours_end": None,
                "digest_local_time": None,
                "categories": {},
                "is_enabled": True,
            }
        ],
        "system_settings": {
            "retention_policy": get_default_policy(),
            "web_app_url": "https://app.adpulse.su/tma",
        },
    }
    counts = {
        "accounts": 1,
        "offers": 1,
        "offer_rules": 1,
        "observer_settings": 1,
        "operator_display_settings": 1,
        "recipients": 1,
        "recipient_preferences": 1,
        "system_settings": 1,
    }
    hashes = {
        name: hashlib.sha256(_canonical_json(payload).encode("utf-8")).hexdigest()
        for name, payload in sections.items()
    }
    return {
        "schema_version": "adoption-bundle/v1",
        "exported_at": "2026-08-11T00:00:00Z",
        "source_fingerprint": "a" * 64,
        "entity_counts": counts,
        "section_sha256": hashes,
        "sections": sections,
    }


def _write_private(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    path.chmod(0o600)


def _write_profile_seed(profile: Path) -> None:
    profile.mkdir(mode=0o700)
    profile.chmod(0o700)
    _write_private(profile / ".fb-agent-vision-profile-v1", "fb-agent-vision-profile-v1\n")
    browser = profile / "browser"
    browser.mkdir(mode=0o700)
    browser.chmod(0o700)
    default_profile = browser / "Default"
    default_profile.mkdir(mode=0o700)
    default_profile.chmod(0o700)
    _write_private(default_profile / "Preferences", "{}\n")


def _assert_tree_ownership(root: Path, *, uid: int, gid: int) -> None:
    if not root.is_dir() or root.is_symlink():
        raise RehearsalError(f"canonical Vision config is not a directory: {root}")
    for path in (root, *sorted(root.rglob("*"))):
        metadata = path.lstat()
        if (metadata.st_uid, metadata.st_gid) != (uid, gid):
            raise RehearsalError(
                f"canonical Vision ownership mismatch: {path} "
                f"is {metadata.st_uid}:{metadata.st_gid}, expected {uid}:{gid}"
            )
        if stat.S_ISLNK(metadata.st_mode):
            raise RehearsalError(f"canonical Vision config contains a symlink: {path}")


def _source_environment(cluster_id: str) -> str:
    values = {
        "FB_AGENT_BOOTSTRAP_CLUSTER_ID": cluster_id,
        "POSTGRES_PASSWORD": "rehearsal_postgres_password_0123456789",
        "ENCRYPTION_KEY": "UOGaDCkFFfSv7XMSdwQq_rqmossFFl8wSG7z69_5nO0=",
        "ENCRYPTION_KEY_VERIFY": "gAAAAABqZwkRi9J37pVDxsdD0LHKWe_L6EkbhQVu1yKi_N43MdYL_I1IV_-5gsOOBXzCRMY9phj3dpLhDtQCsDcJPQKhEQjiRNeb6RuubyvM6vuxf6dgr30=",
        "API_KEY": API_KEY,
        "TELEGRAM_BOT_TOKEN": "123456789:rehearsal-only-token",
        "TELEGRAM_WEBHOOK_SECRET": TELEGRAM_WEBHOOK_SECRET,
        "ALERTMANAGER_WEBHOOK_SECRET": ALERTMANAGER_WEBHOOK_SECRET,
        "TELEGRAM_OIDC_CLIENT_ID": "123456789",
        "TELEGRAM_OIDC_CLIENT_SECRET": "rehearsal_oidc_secret_0123456789abcdef",
        "DESKTOP_OWNER_TELEGRAM_USER_ID": "1",
        "VISION_X_TOKEN": "rehearsal-vision-token",
        "VISION_PROFILE_ID": "rehearsal-profile",
    }
    return "".join(f"{key}={value}\n" for key, value in sorted(values.items()))


def _wait_for_registry() -> None:
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        try:
            with urllib.request.urlopen("http://127.0.0.1:5000/v2/", timeout=2) as response:
                if response.status == 200:
                    return
        except (OSError, urllib.error.URLError):
            time.sleep(0.5)
    raise RehearsalError("local content registry did not become ready")


def _build_stub_image(source_root: Path, browser_image: str, tag: str) -> str:
    _run(
        [
            "docker",
            "build",
            "--build-arg",
            f"BASE_IMAGE={browser_image}",
            "--file",
            "tests/rehearsal/browser-stub/Dockerfile",
            "--tag",
            tag,
            ".",
        ],
        cwd=source_root,
    )
    _run(["docker", "push", tag])
    inspected = _run(
        ["docker", "image", "inspect", "--format", "{{index .RepoDigests 0}}", tag],
        capture=True,
    ).stdout.strip()
    if not IMMUTABLE_IMAGE.fullmatch(inspected):
        raise RehearsalError("rehearsal stub did not resolve to an immutable digest")
    return inspected


def _wait_for_telegram_stub(container: str) -> None:
    deadline = time.monotonic() + 30
    command = (
        "const n=require('net').connect(18080,'127.0.0.1');"
        "n.on('connect',()=>{n.end();process.exit(0)});"
        "n.on('error',()=>process.exit(1));setTimeout(()=>process.exit(1),1000)"
    )
    while time.monotonic() < deadline:
        status = _run(
            ["docker", "exec", container, "node", "-e", command],
            check=False,
        )
        if status.returncode == 0:
            return
        time.sleep(0.5)
    raise RehearsalError("Telegram rehearsal stub did not become ready")


def _rehearsal_failpoints(bundle: Path) -> list[str]:
    result = _run(
        [sys.executable, "-B", os.fspath(bundle), "deploy", "--list-failpoints"],
        capture=True,
    )
    try:
        payload = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise RehearsalError("fbctl returned unreadable rehearsal failpoints") from exc
    if not isinstance(payload, dict):
        raise RehearsalError("fbctl did not export its ordered rehearsal failpoints")
    failpoints = payload.get("failpoints")
    if payload.get("schema") != "fb-agent-rehearsal-failpoints/v1" or not isinstance(
        failpoints, list
    ):
        raise RehearsalError("fbctl did not export its ordered rehearsal failpoints")
    if not failpoints or any(not isinstance(step, str) or not step for step in failpoints):
        raise RehearsalError("fbctl exported invalid rehearsal failpoints")
    if len(set(failpoints)) != len(failpoints):
        raise RehearsalError("fbctl exported duplicate rehearsal failpoints")
    return failpoints


def _validate_rehearsal_request(
    scenario: str,
    *,
    shard_index: int,
    shard_count: int,
) -> None:
    if scenario not in SCENARIOS:
        raise RehearsalError(f"unsupported rehearsal scenario: {scenario}")
    if shard_count < 1:
        raise RehearsalError("shard count must be at least 1")
    if shard_index < 0 or shard_index >= shard_count:
        raise RehearsalError("shard index must be between 0 and shard count - 1")
    if scenario != "failpoints" and (shard_index != 0 or shard_count != 1):
        raise RehearsalError("shard options are available only for the failpoints scenario")


def _partition_failpoints(
    failpoints: list[str],
    *,
    shard_index: int,
    shard_count: int,
) -> list[str]:
    if shard_count < 1:
        raise RehearsalError("shard count must be at least 1")
    if shard_index < 0 or shard_index >= shard_count:
        raise RehearsalError("shard index must be between 0 and shard count - 1")
    return failpoints[shard_index::shard_count]


def _assert_workers_off(cluster_id: str) -> None:
    result = _run(
        [
            "docker",
            "ps",
            "--filter",
            f"label=com.fb-agent.cluster-id={cluster_id}",
            "--format",
            '{{.Label "com.fb-agent.service"}}',
        ],
        capture=True,
    )
    running = set(result.stdout.split())
    leaked = sorted(running & WORKER_SERVICES)
    if leaked:
        raise RehearsalError(f"failure cleanup left workers running: {','.join(leaked)}")


def _deploy_arguments(bundle: Path, root: Path, *extra: str) -> list[str]:
    return [
        sys.executable,
        "-B",
        os.fspath(bundle),
        "deploy",
        "--root",
        os.fspath(root),
        "--rehearsal",
        *extra,
    ]


def _exercise_failpoints(
    bundle: Path,
    root: Path,
    cluster_id: str,
    *,
    shard_index: int,
    shard_count: int,
) -> None:
    failpoints = _rehearsal_failpoints(bundle)
    try:
        stopped_index = failpoints.index("stop_runtime")
    except ValueError as exc:
        raise RehearsalError("fbctl failpoints omit stop_runtime") from exc
    selected = _partition_failpoints(
        failpoints,
        shard_index=shard_index,
        shard_count=shard_count,
    )
    if not selected:
        raise RehearsalError("failpoint shard is empty")
    print(f"single-slot failpoint shard: {shard_index + 1}/{shard_count} [{','.join(selected)}]")
    positions = {step: index for index, step in enumerate(failpoints)}
    for step in selected:
        failed = _run(
            _deploy_arguments(
                bundle,
                root,
                "--enable-scanning",
                "--fail-after-step",
                step,
            ),
            check=False,
            capture=True,
        )
        if failed.returncode == 0 or f'"step": "{step}"' not in failed.stderr:
            raise RehearsalError(f"deploy failpoint did not stop after {step}")
        if positions[step] >= stopped_index:
            _assert_workers_off(cluster_id)
        _run(_deploy_arguments(bundle, root, "--enable-scanning"))


def _exercise_scenario(
    *,
    scenario: str,
    bundle: Path,
    root: Path,
    cluster_id: str,
    telegram: str,
    fingerprint: str,
    shard_index: int,
    shard_count: int,
) -> None:
    if scenario == "failpoints":
        _exercise_failpoints(
            bundle,
            root,
            cluster_id,
            shard_index=shard_index,
            shard_count=shard_count,
        )
        _final_smoke()
        return

    if scenario == "acceptance":
        _run(_deploy_arguments(bundle, root, "--enable-scanning"))
        _final_smoke()
        _exercise_notification_lifecycle(telegram, fingerprint)
        _run(_deploy_arguments(bundle, root))
        _final_smoke()
        return

    if scenario != "full":  # pragma: no cover - validated at the public boundary
        raise RehearsalError(f"unsupported rehearsal scenario: {scenario}")
    _exercise_failpoints(
        bundle,
        root,
        cluster_id,
        shard_index=0,
        shard_count=1,
    )
    _final_smoke()
    _exercise_notification_lifecycle(telegram, fingerprint)
    _run(_deploy_arguments(bundle, root))
    _final_smoke()


def _api_json(path: str, *, api_key: bool = False) -> tuple[int, Any]:
    headers = {"X-API-Key": API_KEY} if api_key else {}
    request = urllib.request.Request(f"http://127.0.0.1:18100{path}", headers=headers)
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def _final_smoke() -> None:
    for path, authenticated in (
        ("/healthz", False),
        ("/readyz", False),
        ("/system-readyz", False),
        ("/api/operator/snapshot", True),
        ("/api/settings/telegram/diagnostics", True),
    ):
        status, payload = _api_json(path, api_key=authenticated)
        if status != 200 or not isinstance(payload, dict):
            raise RehearsalError(f"release smoke failed for {path}: HTTP {status}")
    status, diagnostics = _api_json("/api/settings/telegram/diagnostics", api_key=True)
    generation = diagnostics.get("webhook_generation") if status == 200 else None
    if not isinstance(generation, int) or generation <= 0:
        raise RehearsalError("Telegram diagnostics have no applied generation")
    webhook = urllib.request.Request(
        (
            "http://127.0.0.1:18100/api/v1/integrations/telegram/webhook"
            f"?bot_generation={generation}"
        ),
        data=json.dumps({"update_id": 9000001}, separators=(",", ":")).encode(),
        headers={
            "Content-Type": "application/json",
            "X-Telegram-Bot-Api-Secret-Token": TELEGRAM_WEBHOOK_SECRET,
        },
        method="POST",
    )
    with urllib.request.urlopen(webhook, timeout=20) as response:
        if response.status != 204:
            raise RehearsalError(f"Telegram webhook inbox returned HTTP {response.status}")


def _telegram_evidence(container: str) -> list[dict[str, Any]]:
    script = (
        "fetch('http://127.0.0.1:18080/evidence')"
        ".then(r=>r.json()).then(v=>process.stdout.write(JSON.stringify(v)))"
    )
    result = _run(
        ["docker", "exec", container, "node", "-e", script],
        capture=True,
    )
    payload = json.loads(result.stdout)
    calls = payload.get("calls") if isinstance(payload, dict) else None
    if not isinstance(calls, list):
        raise RehearsalError("Telegram stub returned invalid evidence")
    return [item for item in calls if isinstance(item, dict)]


def _reset_telegram_evidence(container: str) -> None:
    script = (
        "fetch('http://127.0.0.1:18080/evidence/reset',{method:'POST'})"
        ".then(r=>{if(r.status!==204)process.exit(2)})"
    )
    _run(["docker", "exec", container, "node", "-e", script])


def _post_alertmanager(status: str, fingerprint: str) -> None:
    payload = {
        "version": "4",
        "status": status,
        "receiver": "durable-notification-plane",
        "alerts": [
            {
                "status": status,
                "labels": {
                    "alertname": "RehearsalNotificationLifecycle",
                    "severity": "critical",
                    "service": "release-rehearsal",
                },
                "annotations": {"summary": "Disposable release rehearsal alert"},
                "startsAt": "2026-08-11T00:00:00Z",
                "fingerprint": fingerprint,
            }
        ],
    }
    request = urllib.request.Request(
        "http://127.0.0.1:18100/api/v1/integrations/alertmanager/webhook",
        data=_canonical_json(payload).encode(),
        headers={
            "Authorization": f"Bearer {ALERTMANAGER_WEBHOOK_SECRET}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=20) as response:
        if response.status != 204:
            raise RehearsalError(f"Alertmanager webhook returned HTTP {response.status}")


def _wait_for_telegram_method(container: str, method: str, *, timeout: float = 30) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if any(
            call.get("method") == method and call.get("lifecycle") is True
            for call in _telegram_evidence(container)
        ):
            return
        time.sleep(0.5)
    raise RehearsalError(f"Telegram delivery did not execute {method}")


def _exercise_notification_lifecycle(container: str, fingerprint: str) -> None:
    _reset_telegram_evidence(container)
    _post_alertmanager("firing", fingerprint)
    _wait_for_telegram_method(container, "sendMessage")
    _post_alertmanager("resolved", fingerprint)
    _wait_for_telegram_method(container, "editMessageText")
    calls = _telegram_evidence(container)
    sends = [
        call
        for call in calls
        if call.get("method") == "sendMessage" and call.get("lifecycle") is True
    ]
    edits = [
        call
        for call in calls
        if call.get("method") == "editMessageText" and call.get("lifecycle") is True
    ]
    if len(sends) != 1 or not edits:
        raise RehearsalError("Telegram incident slot did not preserve one-message lifecycle")
    if sends[0].get("message_id") != 42 or any(edit.get("message_id") != 42 for edit in edits):
        raise RehearsalError("Telegram recovery did not edit the original message slot")


def _cleanup(root: Path, registry: str, telegram: str, cluster_id: str) -> None:
    _run(["docker", "rm", "-f", telegram], check=False)
    containers = _run(
        ["docker", "ps", "-aq", "--filter", f"label=com.fb-agent.cluster-id={cluster_id}"],
        check=False,
        capture=True,
    ).stdout.split()
    if containers:
        _run(["docker", "rm", "-f", *containers], check=False)
    for volume in MANAGED_VOLUMES:
        _run(["docker", "volume", "rm", "-f", volume], check=False)
    _run(["docker", "network", "rm", PLATFORM_NETWORK], check=False)
    _run(["docker", "rm", "-f", registry], check=False)
    if root.is_dir() and root.parent == Path(tempfile.gettempdir()):
        shutil.rmtree(root)


def rehearse(
    bundle: Path,
    release_manifest: Path,
    source_root: Path,
    *,
    scenario: str = "full",
    shard_index: int = 0,
    shard_count: int = 1,
) -> None:
    _validate_rehearsal_request(
        scenario,
        shard_index=shard_index,
        shard_count=shard_count,
    )
    _require_ci_acknowledgement()
    release = _load_release(release_manifest)
    _run([sys.executable, "-B", os.fspath(bundle), "--help"])
    run_id = re.sub(r"[^0-9A-Za-z_.-]", "-", os.environ.get("GITHUB_RUN_ID", "local"))
    cluster_id = hashlib.sha256(f"fb-agent-rehearsal:{run_id}".encode()).hexdigest()[:32]
    root = Path(tempfile.gettempdir()) / f"fb-agent-rehearsal-{run_id}"
    registry = f"fb-agent-rehearsal-registry-{run_id}"
    telegram = f"fb-agent-rehearsal-telegram-{run_id}"
    if root.exists():
        raise RehearsalError(f"rehearsal root already exists: {root}")
    root.mkdir(mode=0o755)
    try:
        _run(
            [
                "docker",
                "run",
                "--detach",
                "--rm",
                "--name",
                registry,
                "--publish",
                "127.0.0.1:5000:5000",
                REGISTRY_IMAGE,
            ]
        )
        _wait_for_registry()
        stub_tag = "localhost:5000/fb-agent-rehearsal:external-stubs"
        stub_ref = _build_stub_image(
            source_root,
            str(release["images"]["BROWSER_AGENT_IMAGE"]),
            stub_tag,
        )
        rehearsal_release = json.loads(json.dumps(release))
        rehearsal_release["images"]["BROWSER_AGENT_IMAGE"] = stub_ref
        rehearsal_release["images"]["DESKTOP_WEBTOP_IMAGE"] = stub_ref
        rehearsal_manifest = root / "release.json"
        _write_private(rehearsal_manifest, _canonical_json(rehearsal_release) + "\n")
        rehearsal_bundle = root / "fbctl.pyz"
        _run(
            [
                os.fspath(source_root / "scripts/fbctl"),
                "bundle",
                "--output",
                os.fspath(rehearsal_bundle),
                "--release-id",
                str(release["release_id"]),
                "--release-manifest",
                os.fspath(rehearsal_manifest),
                "--source-root",
                os.fspath(source_root),
            ],
            cwd=source_root,
        )
        source_env = root / "source.env"
        _write_private(source_env, _source_environment(cluster_id))
        adoption = root / "adoption.json"
        _write_private(adoption, _canonical_json(build_adoption_bundle()) + "\n")
        profile = root / "desktop-profile-seed"
        _write_profile_seed(profile)
        _run(
            [
                sys.executable,
                "-B",
                os.fspath(rehearsal_bundle),
                "bootstrap",
                "--root",
                os.fspath(root),
                "--source-env",
                os.fspath(source_env),
                "--adoption-bundle",
                os.fspath(adoption),
                "--desktop-profile-seed",
                os.fspath(profile),
                "--rehearsal",
            ]
        )
        _assert_tree_ownership(root / "shared" / "vision-config", uid=1000, gid=1000)
        _run(
            [
                "docker",
                "run",
                "--detach",
                "--name",
                telegram,
                "--network",
                PLATFORM_NETWORK,
                "--network-alias",
                "telegram-stub",
                "--env",
                "REHEARSAL_ROLE=telegram",
                stub_ref,
            ]
        )
        _wait_for_telegram_stub(telegram)
        _exercise_scenario(
            scenario=scenario,
            bundle=rehearsal_bundle,
            root=root,
            cluster_id=cluster_id,
            telegram=telegram,
            fingerprint=hashlib.sha256(f"notification:{run_id}".encode()).hexdigest()[:32],
            shard_index=shard_index,
            shard_count=shard_count,
        )
    finally:
        _cleanup(root, registry, telegram, cluster_id)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("bundle", type=Path)
    parser.add_argument("release_manifest", type=Path)
    parser.add_argument("--source-root", type=Path, default=Path.cwd())
    parser.add_argument("--scenario", choices=SCENARIOS, default="full")
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--shard-count", type=int, default=1)
    args = parser.parse_args(argv)
    try:
        rehearse(
            args.bundle.resolve(strict=True),
            args.release_manifest.resolve(strict=True),
            args.source_root.resolve(strict=True),
            scenario=args.scenario,
            shard_index=args.shard_index,
            shard_count=args.shard_count,
        )
    except (OSError, RehearsalError, subprocess.CalledProcessError) as exc:
        print(f"single-slot rehearsal failed: {type(exc).__name__}", file=sys.stderr)
        return 1
    print("single-slot production rehearsal: READY")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
