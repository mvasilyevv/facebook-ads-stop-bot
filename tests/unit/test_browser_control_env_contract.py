from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[2]
VALIDATOR = ROOT / "scripts/browser-control-env.sh"
APP_COMPOSE = ROOT / "deploy/compose/docker-compose.app.yml"
DESKTOP_COMPOSE = ROOT / "deploy/compose/docker-compose.desktop-agent.yml"
INFRA_COMPOSE = ROOT / "deploy/compose/docker-compose.infra.yml"
MAINTENANCE_SECRET = "browser-maintenance-" + "m" * 48
AUTOPAUSE_SECRET = "browser-autopause-" + "a" * 48
META_API_SECRET = "browser-meta-api-" + "o" * 48
CAMPAIGN_CREATOR_SECRET = "browser-campaign-creator-" + "c" * 48
AUTHORITY_TOKEN = "browser-authority-" + "t" * 48
ALL_SECRETS = (
    MAINTENANCE_SECRET,
    AUTOPAUSE_SECRET,
    META_API_SECRET,
    CAMPAIGN_CREATOR_SECRET,
    AUTHORITY_TOKEN,
)
CANONICAL_AUTHORITY_URL = "https://app.adpulse.su/api/v1/internal/browser-operations/consume"
CANONICAL_MAINTENANCE_AUTHORITY_URL = (
    "https://app.adpulse.su/api/v1/internal/browser-maintenance/consume"
)


def _write_control_env(path: Path, *, extra: str = "") -> None:
    path.write_text(
        "# API/browser-agent only\n"
        f"BROWSER_MAINTENANCE_CAPABILITY_SECRET={MAINTENANCE_SECRET}\n"
        f"BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE={AUTOPAUSE_SECRET}\n"
        f"BROWSER_OPERATION_CAPABILITY_SECRET_META_API={META_API_SECRET}\n"
        "BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR="
        f"{CAMPAIGN_CREATOR_SECRET}\n"
        f"BROWSER_AUTHORITY_CONSUMER_TOKEN={AUTHORITY_TOKEN}\n"
        f"{extra}",
        encoding="utf-8",
    )
    path.chmod(0o600)


def _validate(
    path: Path,
    *,
    expected_uid: int | None = None,
    xtrace: bool = False,
) -> subprocess.CompletedProcess[str]:
    uid = os.getuid() if expected_uid is None else expected_uid
    bash_args = ["bash"]
    if xtrace:
        bash_args.append("-x")
    return subprocess.run(
        [
            *bash_args,
            "-c",
            'source "$1"; browser_control_env_require "$2" "$3"',
            "browser-control-test",
            str(VALIDATOR),
            str(path),
            str(uid),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _validate_scoped(path: Path, key: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            "bash",
            "-c",
            'source "$1"; browser_scoped_env_require "$2" "$3" "$4"',
            "browser-scoped-test",
            str(VALIDATOR),
            str(path),
            key,
            str(os.getuid()),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


def _env_files(service: dict[str, object]) -> list[str]:
    value = service.get("env_file", [])
    return [value] if isinstance(value, str) else list(value)


def test_private_browser_control_env_is_accepted_without_printing_secret(
    tmp_path: Path,
) -> None:
    control = tmp_path / "browser-control.env"
    _write_control_env(control)

    result = _validate(control)

    assert result.returncode == 0, result.stderr
    for secret in ALL_SECRETS:
        assert secret not in result.stdout
        assert secret not in result.stderr


def test_private_browser_control_env_stays_secret_with_caller_xtrace(
    tmp_path: Path,
) -> None:
    control = tmp_path / "browser-control.env"
    _write_control_env(control)

    result = _validate(control, xtrace=True)

    assert result.returncode == 0, result.stderr
    for secret in ALL_SECRETS:
        assert secret not in result.stdout
        assert secret not in result.stderr


@pytest.mark.parametrize("mode", (0o400, 0o640, 0o644))
def test_browser_control_env_requires_exact_mode_0600(
    tmp_path: Path,
    mode: int,
) -> None:
    control = tmp_path / "browser-control.env"
    _write_control_env(control)
    control.chmod(mode)

    result = _validate(control)

    assert result.returncode != 0
    assert "owner or mode differs" in result.stderr


def test_browser_control_env_rejects_symlink_and_wrong_owner_contract(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.env"
    _write_control_env(target)
    link = tmp_path / "browser-control.env"
    link.symlink_to(target)

    symlink_result = _validate(link)
    owner_result = _validate(target, expected_uid=os.getuid() + 1)

    assert symlink_result.returncode != 0
    assert "non-symlink" in symlink_result.stderr
    assert owner_result.returncode != 0
    assert "owner or mode differs" in owner_result.stderr


@pytest.mark.parametrize(
    "content",
    (
        "",
        "BROWSER_MAINTENANCE_CAPABILITY_SECRET=short\n",
        f"BROWSER_MAINTENANCE_CAPABILITY_SECRET={MAINTENANCE_SECRET}\n",
        f"BROWSER_OPERATION_CAPABILITY_SECRET={AUTOPAUSE_SECRET}\n",
        (
            f"BROWSER_MAINTENANCE_CAPABILITY_SECRET={MAINTENANCE_SECRET}\n"
            f"BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE={AUTOPAUSE_SECRET}\n"
            f"BROWSER_OPERATION_CAPABILITY_SECRET_META_API={META_API_SECRET}\n"
            "BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR="
            f"{CAMPAIGN_CREATOR_SECRET}\n"
            f"BROWSER_AUTHORITY_CONSUMER_TOKEN={AUTHORITY_TOKEN}\n"
            "UNRELATED_SECRET=leak\n"
        ),
        (
            f"BROWSER_MAINTENANCE_CAPABILITY_SECRET={MAINTENANCE_SECRET}\n"
            f"BROWSER_MAINTENANCE_CAPABILITY_SECRET={MAINTENANCE_SECRET}\n"
            f"BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE={AUTOPAUSE_SECRET}\n"
            f"BROWSER_OPERATION_CAPABILITY_SECRET_META_API={META_API_SECRET}\n"
            "BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR="
            f"{CAMPAIGN_CREATOR_SECRET}\n"
            f"BROWSER_AUTHORITY_CONSUMER_TOKEN={AUTHORITY_TOKEN}\n"
        ),
        (
            f"BROWSER_MAINTENANCE_CAPABILITY_SECRET={MAINTENANCE_SECRET}\n"
            "BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE="
            f"{MAINTENANCE_SECRET}\n"
            f"BROWSER_OPERATION_CAPABILITY_SECRET_META_API={META_API_SECRET}\n"
            "BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR="
            f"{CAMPAIGN_CREATOR_SECRET}\n"
            f"BROWSER_AUTHORITY_CONSUMER_TOKEN={AUTHORITY_TOKEN}\n"
        ),
    ),
)
def test_browser_control_env_rejects_missing_weak_extra_or_duplicate_content(
    tmp_path: Path,
    content: str,
) -> None:
    control = tmp_path / "browser-control.env"
    control.write_text(content, encoding="utf-8")
    control.chmod(0o600)

    result = _validate(control)

    assert result.returncode != 0
    for secret in ALL_SECRETS:
        assert secret not in result.stdout
        assert secret not in result.stderr


@pytest.mark.parametrize(
    ("key", "secret"),
    (
        ("BROWSER_MAINTENANCE_CAPABILITY_SECRET", MAINTENANCE_SECRET),
        ("BROWSER_OPERATION_CAPABILITY_SECRET", AUTOPAUSE_SECRET),
        ("BROWSER_AUTHORITY_CONSUMER_TOKEN", AUTHORITY_TOKEN),
    ),
)
def test_scoped_browser_env_accepts_exactly_one_capability(
    tmp_path: Path,
    key: str,
    secret: str,
) -> None:
    scoped = tmp_path / "browser-scoped.env"
    scoped.write_text(f"{key}={secret}\n", encoding="utf-8")
    scoped.chmod(0o600)

    assert _validate_scoped(scoped, key).returncode == 0
    for other_key in (
        "BROWSER_MAINTENANCE_CAPABILITY_SECRET",
        "BROWSER_OPERATION_CAPABILITY_SECRET",
        "BROWSER_AUTHORITY_CONSUMER_TOKEN",
    ):
        if other_key != key:
            assert _validate_scoped(scoped, other_key).returncode != 0


def test_capability_secrets_are_injected_only_into_authorized_services() -> None:
    app = yaml.safe_load(APP_COMPOSE.read_text(encoding="utf-8"))["services"]
    desktop = yaml.safe_load(DESKTOP_COMPOSE.read_text(encoding="utf-8"))["services"]
    infra = yaml.safe_load(INFRA_COMPOSE.read_text(encoding="utf-8"))["services"]
    browser_env = "${BROWSER_CONTROL_ENV_FILE:?set BROWSER_CONTROL_ENV_FILE}"
    maintenance_env = "${BROWSER_MAINTENANCE_ENV_FILE:?set BROWSER_MAINTENANCE_ENV_FILE}"
    signer_envs = {
        "autopause_worker": ("${BROWSER_AUTOPAUSE_ENV_FILE:?set BROWSER_AUTOPAUSE_ENV_FILE}"),
        "meta_api": "${BROWSER_META_API_ENV_FILE:?set BROWSER_META_API_ENV_FILE}",
        "campaign_creator": (
            "${BROWSER_CAMPAIGN_CREATOR_ENV_FILE:?set BROWSER_CAMPAIGN_CREATOR_ENV_FILE}"
        ),
    }
    authority_env = "${BROWSER_AUTHORITY_ENV_FILE:?set BROWSER_AUTHORITY_ENV_FILE}"
    app_env = "${APP_ENV_FILE:?set APP_ENV_FILE}"

    assert maintenance_env in _env_files(app["api"])
    assert authority_env in _env_files(app["api"])
    for name, service in app.items():
        env_files = _env_files(service)
        assert browser_env not in env_files, name
        for signer_name, signer_env in signer_envs.items():
            assert (signer_env in env_files) is (name == signer_name), name
        if name != "api":
            assert maintenance_env not in env_files, name
            assert authority_env not in env_files, name
    for name, service in infra.items():
        assert browser_env not in _env_files(service), name
        assert maintenance_env not in _env_files(service), name
        assert authority_env not in _env_files(service), name
        for signer_env in signer_envs.values():
            assert signer_env not in _env_files(service), name

    browser_agent = desktop["browser-agent"]
    assert _env_files(browser_agent) == [browser_env]
    assert app_env not in _env_files(browser_agent)
    assert set(browser_agent["environment"]) == {
        "GRPC_PORT",
        "WORKER_METRICS_PORT",
        "BROWSER_AUTHORITY_CONSUME_URL",
        "BROWSER_MAINTENANCE_CONSUME_URL",
        "BROWSER_AGENT_AM_COLUMNS_QS",
    }
    assert all(not key.startswith("REDIS") for key in browser_agent["environment"])


def test_browser_agent_compose_environment_matches_runtime_consumers() -> None:
    browser_agent = yaml.safe_load(DESKTOP_COMPOSE.read_text(encoding="utf-8"))["services"][
        "browser-agent"
    ]
    provided = set(browser_agent["environment"])
    provided.add("BROWSER_MAINTENANCE_CAPABILITY_SECRET")
    provided.update(
        {
            "BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE",
            "BROWSER_OPERATION_CAPABILITY_SECRET_META_API",
            "BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR",
            "BROWSER_AUTHORITY_CONSUMER_TOKEN",
        }
    )
    consumed: set[str] = set()
    for path in (ROOT / "services/browser-agent/src").rglob("*.ts"):
        if path.name.endswith(".test.ts"):
            continue
        source = path.read_text(encoding="utf-8")
        for dotted, indexed, injected in re.findall(
            r"""(?:process\.env(?:\.([A-Z][A-Z0-9_]*)|\[['"]([A-Z][A-Z0-9_]*)['"]\])|environment\.([A-Z][A-Z0-9_]*))""",
            source,
        ):
            consumed.add(dotted or indexed or injected)

    assert consumed == provided


def test_desktop_wrappers_export_only_the_canonical_https_authority_url() -> None:
    assert "?" not in CANONICAL_AUTHORITY_URL
    assert AUTHORITY_TOKEN not in CANONICAL_AUTHORITY_URL
    assert "?" not in CANONICAL_MAINTENANCE_AUTHORITY_URL
    assert AUTHORITY_TOKEN not in CANONICAL_MAINTENANCE_AUTHORITY_URL
    for name in ("platform-desktop-compose.sh", "platform-desktop-release.sh"):
        source = (ROOT / "scripts" / name).read_text(encoding="utf-8")
        assert CANONICAL_AUTHORITY_URL in source
        assert CANONICAL_MAINTENANCE_AUTHORITY_URL in source
        assert "export BROWSER_AGENT_AM_COLUMNS_QS BROWSER_AUTHORITY_CONSUME_URL" in source
        assert "export BROWSER_MAINTENANCE_CONSUME_URL" in source
        assert "BROWSER_AUTHORITY_CONSUMER_TOKEN" not in source


def test_every_production_caller_uses_the_shared_private_file_validator() -> None:
    compose_names = (
        "docker-compose.app.yml",
        "docker-compose.desktop-agent.yml",
    )
    callers = {
        path
        for path in (ROOT / "scripts").glob("*.sh")
        if any(
            marker in path.read_text(encoding="utf-8")
            for marker in (*compose_names, "BROWSER_CONTROL_ENV_FILE")
        )
    }
    assert callers
    for path in callers:
        source = path.read_text(encoding="utf-8")
        assert "browser-control-env.sh" in source, path.relative_to(ROOT)
        assert "browser_control_env_require" in source, path.relative_to(ROOT)
        if "BROWSER_MAINTENANCE_ENV_FILE" in source:
            assert "browser_maintenance_env_require" in source, path.relative_to(ROOT)
        if any(
            marker in source
            for marker in (
                "BROWSER_AUTOPAUSE_ENV_FILE",
                "BROWSER_META_API_ENV_FILE",
                "BROWSER_CAMPAIGN_CREATOR_ENV_FILE",
            )
        ):
            assert "browser_operation_env_require" in source, path.relative_to(ROOT)
        if "BROWSER_AUTHORITY_ENV_FILE" in source:
            assert "browser_authority_env_require" in source, path.relative_to(ROOT)

    installer = (ROOT / "scripts/install-release-reconciler.sh").read_text(encoding="utf-8")
    assert "browser-control-env.sh" in installer
