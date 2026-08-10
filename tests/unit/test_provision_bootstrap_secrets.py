from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    path = ROOT / "scripts/provision-bootstrap-secrets.py"
    spec = importlib.util.spec_from_file_location("provision_bootstrap_secrets", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


MODULE = _load_module()


def _private_source(tmp_path: Path, password: str = "fb_stop_bot") -> Path:
    source = tmp_path / "shared.env"
    source.write_text(
        f"POSTGRES_DB=fb_stop_bot\nPOSTGRES_PASSWORD={password}\n",
        encoding="utf-8",
    )
    source.chmod(0o600)
    return source


def test_bootstrap_secret_is_atomic_and_reused_across_release_retries(
    tmp_path: Path,
) -> None:
    source = _private_source(tmp_path)
    output = tmp_path / "bootstrap-secrets.env"
    lock = tmp_path / "bootstrap-secrets.lock"
    owner_uid = os.getuid()

    first_cluster, created = MODULE.provision(
        source=source,
        output=output,
        lock_path=lock,
        owner_uid=owner_uid,
    )
    first_bytes = output.read_bytes()
    second_cluster, created_again = MODULE.provision(
        source=source,
        output=output,
        lock_path=lock,
        owner_uid=owner_uid,
    )

    assert created is True
    assert created_again is False
    assert second_cluster == first_cluster
    assert output.read_bytes() == first_bytes
    assert output.stat().st_mode & 0o777 == 0o600
    assert lock.stat().st_mode & 0o777 == 0o600
    assert list(tmp_path.glob(".bootstrap-secrets.env.*")) == []
    values = MODULE.parse_values(first_bytes.decode())
    for key in MODULE.DURABLE_GENERATED_SECRETS:
        assert len(values[key]) >= MODULE.DURABLE_GENERATED_SECRETS[key]


def test_secure_source_password_cannot_diverge_from_owned_cluster(tmp_path: Path) -> None:
    source = _private_source(tmp_path, "a" * 32)
    output = tmp_path / "bootstrap-secrets.env"
    lock = tmp_path / "bootstrap-secrets.lock"
    owner_uid = os.getuid()
    MODULE.provision(
        source=source,
        output=output,
        lock_path=lock,
        owner_uid=owner_uid,
    )
    source.write_text(
        f"POSTGRES_DB=fb_stop_bot\nPOSTGRES_PASSWORD={'b' * 32}\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="conflicts with durable bootstrap state"):
        MODULE.provision(
            source=source,
            output=output,
            lock_path=lock,
            owner_uid=owner_uid,
        )


def test_symlinked_bootstrap_secret_is_rejected(tmp_path: Path) -> None:
    source = _private_source(tmp_path)
    target = tmp_path / "target"
    target.write_text("do-not-overwrite", encoding="utf-8")
    output = tmp_path / "bootstrap-secrets.env"
    output.symlink_to(target)

    with pytest.raises(ValueError, match="regular file"):
        MODULE.provision(
            source=source,
            output=output,
            lock_path=tmp_path / "bootstrap-secrets.lock",
            owner_uid=os.getuid(),
        )


def test_legacy_bootstrap_state_is_atomically_upgraded_with_durable_secrets(
    tmp_path: Path,
) -> None:
    source = _private_source(tmp_path, "p" * 32)
    output = tmp_path / "bootstrap-secrets.env"
    output.write_text(
        (f"# legacy\nFB_AGENT_BOOTSTRAP_CLUSTER_ID={'c' * 32}\nPOSTGRES_PASSWORD={'p' * 32}\n"),
        encoding="utf-8",
    )
    output.chmod(0o600)

    cluster_id, created = MODULE.provision(
        source=source,
        output=output,
        lock_path=tmp_path / "bootstrap-secrets.lock",
        owner_uid=os.getuid(),
    )

    assert created is False
    assert cluster_id == "c" * 32
    values = MODULE.parse_values(output.read_text(encoding="utf-8"))
    for key, minimum in MODULE.DURABLE_GENERATED_SECRETS.items():
        assert len(values[key]) >= minimum


def test_browser_control_env_contains_only_the_complete_independent_keyring(
    tmp_path: Path,
) -> None:
    source = _private_source(tmp_path)
    bootstrap = tmp_path / "bootstrap-secrets.env"
    MODULE.provision(
        source=source,
        output=bootstrap,
        lock_path=tmp_path / "bootstrap-secrets.lock",
        owner_uid=os.getuid(),
    )
    browser_control = tmp_path / "browser-control.env"

    MODULE.write_browser_control_env(
        bootstrap_secrets=bootstrap,
        output=browser_control,
        owner_uid=os.getuid(),
    )

    values = MODULE.parse_values(browser_control.read_text(encoding="utf-8"))
    assert set(values) == {
        "BROWSER_MAINTENANCE_CAPABILITY_SECRET",
        "BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE",
        "BROWSER_OPERATION_CAPABILITY_SECRET_META_API",
        "BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR",
        "BROWSER_AUTHORITY_CONSUMER_TOKEN",
    }
    assert all(len(value) >= 48 for value in values.values())
    assert len(set(values.values())) == len(values)
    assert browser_control.stat().st_mode & 0o777 == 0o600


def test_scoped_browser_envs_contain_only_their_authorized_capability(
    tmp_path: Path,
) -> None:
    source = _private_source(tmp_path)
    bootstrap = tmp_path / "bootstrap-secrets.env"
    MODULE.provision(
        source=source,
        output=bootstrap,
        lock_path=tmp_path / "bootstrap-secrets.lock",
        owner_uid=os.getuid(),
    )
    maintenance = tmp_path / "browser-maintenance.env"
    scoped_outputs = {
        "BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE": (tmp_path / "browser-autopause.env"),
        "BROWSER_OPERATION_CAPABILITY_SECRET_META_API": (tmp_path / "browser-meta-api.env"),
        "BROWSER_OPERATION_CAPABILITY_SECRET_CAMPAIGN_CREATOR": (
            tmp_path / "browser-campaign-creator.env"
        ),
    }
    authority = tmp_path / "browser-authority.env"

    MODULE.write_scoped_browser_env(
        bootstrap_secrets=bootstrap,
        output=maintenance,
        capability_key="BROWSER_MAINTENANCE_CAPABILITY_SECRET",
        owner_uid=os.getuid(),
    )
    for capability_key, output in scoped_outputs.items():
        MODULE.write_scoped_browser_env(
            bootstrap_secrets=bootstrap,
            output=output,
            capability_key=capability_key,
            output_key="BROWSER_OPERATION_CAPABILITY_SECRET",
            owner_uid=os.getuid(),
        )
    MODULE.write_scoped_browser_env(
        bootstrap_secrets=bootstrap,
        output=authority,
        capability_key="BROWSER_AUTHORITY_CONSUMER_TOKEN",
        owner_uid=os.getuid(),
    )

    assert set(MODULE.parse_values(maintenance.read_text(encoding="utf-8"))) == {
        "BROWSER_MAINTENANCE_CAPABILITY_SECRET"
    }
    bootstrap_values = MODULE.parse_values(bootstrap.read_text(encoding="utf-8"))
    signer_values: list[str] = []
    for capability_key, output in scoped_outputs.items():
        values = MODULE.parse_values(output.read_text(encoding="utf-8"))
        assert set(values) == {"BROWSER_OPERATION_CAPABILITY_SECRET"}
        assert values["BROWSER_OPERATION_CAPABILITY_SECRET"] == bootstrap_values[capability_key]
        signer_values.append(values["BROWSER_OPERATION_CAPABILITY_SECRET"])
        assert output.stat().st_mode & 0o777 == 0o600
    assert len(set(signer_values)) == 3
    authority_values = MODULE.parse_values(authority.read_text(encoding="utf-8"))
    assert authority_values == {
        "BROWSER_AUTHORITY_CONSUMER_TOKEN": bootstrap_values["BROWSER_AUTHORITY_CONSUMER_TOKEN"]
    }
    assert maintenance.stat().st_mode & 0o777 == 0o600
    assert authority.stat().st_mode & 0o777 == 0o600


def test_symlinked_browser_control_env_is_rejected(tmp_path: Path) -> None:
    source = _private_source(tmp_path)
    bootstrap = tmp_path / "bootstrap-secrets.env"
    MODULE.provision(
        source=source,
        output=bootstrap,
        lock_path=tmp_path / "bootstrap-secrets.lock",
        owner_uid=os.getuid(),
    )
    target = tmp_path / "target.env"
    target.write_text("do-not-overwrite", encoding="utf-8")
    browser_control = tmp_path / "browser-control.env"
    browser_control.symlink_to(target)

    with pytest.raises(ValueError, match="regular file"):
        MODULE.write_browser_control_env(
            bootstrap_secrets=bootstrap,
            output=browser_control,
            owner_uid=os.getuid(),
        )


def test_scoped_browser_env_rejects_cross_role_output_key(tmp_path: Path) -> None:
    source = _private_source(tmp_path)
    bootstrap = tmp_path / "bootstrap-secrets.env"
    MODULE.provision(
        source=source,
        output=bootstrap,
        lock_path=tmp_path / "bootstrap-secrets.lock",
        owner_uid=os.getuid(),
    )

    with pytest.raises(ValueError, match="does not match its scope"):
        MODULE.write_scoped_browser_env(
            bootstrap_secrets=bootstrap,
            output=tmp_path / "mis-scoped.env",
            capability_key="BROWSER_AUTHORITY_CONSUMER_TOKEN",
            output_key="BROWSER_OPERATION_CAPABILITY_SECRET",
            owner_uid=os.getuid(),
        )


def test_equal_browser_capability_secrets_are_rejected(tmp_path: Path) -> None:
    source = _private_source(tmp_path)
    bootstrap = tmp_path / "bootstrap-secrets.env"
    MODULE.provision(
        source=source,
        output=bootstrap,
        lock_path=tmp_path / "bootstrap-secrets.lock",
        owner_uid=os.getuid(),
    )
    values = MODULE.parse_values(bootstrap.read_text(encoding="utf-8"))
    values["BROWSER_OPERATION_CAPABILITY_SECRET_AUTOPAUSE"] = values[
        "BROWSER_MAINTENANCE_CAPABILITY_SECRET"
    ]
    bootstrap.write_text(MODULE._render_durable_values(values), encoding="utf-8")

    with pytest.raises(ValueError, match="independently scoped"):
        MODULE.write_browser_control_env(
            bootstrap_secrets=bootstrap,
            output=tmp_path / "browser-control.env",
            owner_uid=os.getuid(),
        )


@pytest.mark.parametrize(
    "key",
    sorted(MODULE.FORBIDDEN_SHARED_BROWSER_KEYS),
)
def test_shared_environment_cannot_supply_browser_capability_secret(
    tmp_path: Path,
    key: str,
) -> None:
    source = _private_source(tmp_path)
    source.write_text(
        source.read_text(encoding="utf-8") + key + "=" + "x" * 64 + "\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must not be stored"):
        MODULE.provision(
            source=source,
            output=tmp_path / "bootstrap-secrets.env",
            lock_path=tmp_path / "bootstrap-secrets.lock",
            owner_uid=os.getuid(),
        )
