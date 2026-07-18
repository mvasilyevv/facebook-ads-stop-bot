from __future__ import annotations

import importlib.util
import stat
from pathlib import Path

import pytest


def _load_module():
    path = Path(__file__).parents[2] / "scripts" / "configure-panel-oidc.py"
    spec = importlib.util.spec_from_file_location("configure_panel_oidc", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


OIDC = _load_module()


def test_configure_atomically_upserts_credentials_without_printing_them(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("KEEP=value\nTELEGRAM_OIDC_CLIENT_ID=old\n", encoding="utf-8")
    env.chmod(0o600)
    secret = "s" * 48

    OIDC.configure(
        env,
        client_id="123456789",
        client_secret=secret,
        redirect_uri="https://app.adpulse.su/auth/telegram/callback",
    )

    rendered = env.read_text(encoding="utf-8")
    assert rendered.count("TELEGRAM_OIDC_CLIENT_ID=") == 1
    assert "TELEGRAM_OIDC_CLIENT_ID=123456789" in rendered
    assert f"TELEGRAM_OIDC_CLIENT_SECRET={secret}" in rendered
    assert "TELEGRAM_OIDC_REDIRECT_URI=https://app.adpulse.su/auth/telegram/callback" in rendered
    assert "KEEP=value" in rendered
    assert stat.S_IMODE(env.stat().st_mode) == 0o600


def test_configure_rejects_unsafe_file_or_malformed_credentials(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text("KEEP=value\n", encoding="utf-8")
    env.chmod(0o644)
    with pytest.raises(PermissionError, match="mode 600"):
        OIDC.configure(
            env,
            client_id="123",
            client_secret="s" * 48,
            redirect_uri="https://app.adpulse.su/auth/telegram/callback",
        )

    env.chmod(0o600)
    with pytest.raises(ValueError, match="numeric"):
        OIDC.configure(
            env,
            client_id="not-a-number",
            client_secret="s" * 48,
            redirect_uri="https://app.adpulse.su/auth/telegram/callback",
        )
