from __future__ import annotations

import base64
import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    path = ROOT / "scripts" / "sync-caddy-env.py"
    spec = importlib.util.spec_from_file_location("sync_caddy_env_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_sync_preserves_basic_auth_and_atomically_replaces_api_key(tmp_path, monkeypatch) -> None:
    module = _load_module()
    source = tmp_path / "shared.env"
    target = tmp_path / "caddy.env"
    source.write_text(
        "API_KEY=new-server-key\n"
        "DESKTOP_KASM_SERVICE_USER=desktop-user\n"
        "DESKTOP_KASM_SERVICE_PASSWORD=desktop-password\n"
        "POSTGRES_PASSWORD=must-not-copy\n"
    )
    target.write_text(
        "PANEL_BASIC_AUTH_USER=operator\nPANEL_BASIC_AUTH_HASH='$2a$hash'\nAPI_KEY=old-server-key\n"
    )
    target.chmod(0o600)
    fsync_calls: list[int] = []
    real_fsync = module.os.fsync

    def record_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(module.os, "fsync", record_fsync)

    module.sync_caddy_env(source, target)

    rendered = target.read_text()
    assert "PANEL_BASIC_AUTH_USER=operator" in rendered
    assert "PANEL_BASIC_AUTH_HASH='$2a$hash'" in rendered
    assert "API_KEY=new-server-key" in rendered
    encoded = base64.b64encode(b"desktop-user:desktop-password").decode()
    assert f"DESKTOP_KASM_SERVICE_AUTH_B64={encoded}" in rendered
    assert "old-server-key" not in rendered
    assert "POSTGRES_PASSWORD" not in rendered
    assert os.stat(target).st_mode & 0o777 == 0o600
    assert len(fsync_calls) == 2


def test_sync_leaves_target_intact_when_atomic_replace_fails(tmp_path, monkeypatch) -> None:
    module = _load_module()
    source = tmp_path / "shared.env"
    target = tmp_path / "caddy.env"
    source.write_text(
        "API_KEY=new-server-key\n"
        "DESKTOP_KASM_SERVICE_USER=desktop-user\n"
        "DESKTOP_KASM_SERVICE_PASSWORD=desktop-password\n"
    )
    original = "PANEL_BASIC_AUTH_USER=operator\nPANEL_BASIC_AUTH_HASH=hash\n"
    target.write_text(original)
    target.chmod(0o600)

    def fail_replace(_source, _target) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        module.sync_caddy_env(source, target)

    assert target.read_text() == original
    assert not list(tmp_path.glob(".caddy.env.sync-*"))


@pytest.mark.parametrize(
    ("source_text", "target_text", "error"),
    [
        ("OTHER=value\n", "PANEL_BASIC_AUTH_USER=u\nPANEL_BASIC_AUTH_HASH=h\n", "API_KEY"),
        ("API_KEY=key\n", "PANEL_BASIC_AUTH_HASH=h\n", "PANEL_BASIC_AUTH_USER"),
        ("API_KEY=key\n", "PANEL_BASIC_AUTH_USER=u\n", "PANEL_BASIC_AUTH_HASH"),
        (
            "API_KEY=one\nAPI_KEY=two\n",
            "PANEL_BASIC_AUTH_USER=u\nPANEL_BASIC_AUTH_HASH=h\n",
            "duplicate API_KEY",
        ),
    ],
)
def test_sync_fails_closed_for_missing_empty_or_duplicate_keys(
    tmp_path, source_text: str, target_text: str, error: str
) -> None:
    module = _load_module()
    source = tmp_path / "shared.env"
    target = tmp_path / "caddy.env"
    source.write_text(
        source_text
        + "DESKTOP_KASM_SERVICE_USER=desktop-user\n"
        + "DESKTOP_KASM_SERVICE_PASSWORD=desktop-password\n"
    )
    target.write_text(target_text)
    target.chmod(0o600)

    with pytest.raises(ValueError, match=error):
        module.sync_caddy_env(source, target)


def test_installer_syncs_secret_before_caddy_validation() -> None:
    installer = (ROOT / "scripts" / "install-server-units.sh").read_text(encoding="utf-8")

    sync_position = installer.index('python3 "$PROJECT_DIR/scripts/sync-caddy-env.py"')
    validate_position = installer.index("caddy validate --config")
    assert sync_position < validate_position
    assert (
        'readonly SHARED_ENV_FILE="${APP_ENV_OVERRIDE:-$ROOT_DIR/shared/active-app.env}"'
        in installer
    )
    assert "stat -c '%a' \"$SHARED_ENV_FILE\"" in installer


def test_scoped_sync_keeps_the_other_release_credential(tmp_path) -> None:
    module = _load_module()
    source = tmp_path / "candidate.env"
    target = tmp_path / "caddy.env"
    source.write_text(
        "API_KEY=new-api\n"
        "DESKTOP_KASM_SERVICE_USER=new-user\n"
        "DESKTOP_KASM_SERVICE_PASSWORD=new-password\n"
    )
    target.write_text(
        "PANEL_BASIC_AUTH_USER=panel\n"
        "PANEL_BASIC_AUTH_HASH=hash\n"
        "API_KEY=old-api\n"
        "DESKTOP_KASM_SERVICE_AUTH_B64=b2xkOmRlc2t0b3A=\n"
    )
    target.chmod(0o600)

    module.sync_caddy_env(source, target, scope="api")
    api_only = target.read_text()
    assert "API_KEY=new-api" in api_only
    assert "DESKTOP_KASM_SERVICE_AUTH_B64=b2xkOmRlc2t0b3A=" in api_only

    module.sync_caddy_env(source, target, scope="desktop")
    desktop = target.read_text()
    assert "API_KEY=new-api" in desktop
    assert "DESKTOP_KASM_SERVICE_AUTH_B64=bmV3LXVzZXI6bmV3LXBhc3N3b3Jk" in desktop
