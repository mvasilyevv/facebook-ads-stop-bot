from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from core.vision_runtime import (
    VisionConfigurationError,
    VisionRuntimeConfig,
    load_vision_runtime_config,
)


def test_settings_do_not_define_vision_credentials() -> None:
    from core.config import Settings

    assert "vision_x_token" not in Settings.model_fields
    assert "vision_profile_id" not in Settings.model_fields
    assert "vision_api_url" in Settings.model_fields
    assert "vision_cloud_url" in Settings.model_fields


class _Result:
    def __init__(self, row: object | None) -> None:
        self._row = row

    def one_or_none(self) -> object | None:
        return self._row


class _Session:
    def __init__(self, row: object | None) -> None:
        self._row = row

    async def __aenter__(self) -> _Session:
        return self

    async def __aexit__(self, *_args: object) -> None:
        return None

    async def execute(self, _statement: object) -> _Result:
        return _Result(self._row)


def _row(
    *,
    token: str = "encrypted",
    profile_id: str = "profile-1",
    folder_id: str | None = "encrypted-folder",
) -> SimpleNamespace:
    return SimpleNamespace(
        x_token_encrypted=token,
        profile_id=profile_id,
        folder_id_encrypted=folder_id,
        updated_at=datetime(2026, 7, 28, tzinfo=UTC),
    )


@pytest.mark.asyncio
async def test_load_vision_runtime_config_returns_detached_db_credentials(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import core.vision_runtime as module

    monkeypatch.setattr(module, "AsyncSession", lambda _engine: _Session(_row()))
    monkeypatch.setattr(
        module,
        "decrypt",
        lambda token: "folder-1" if token == "encrypted-folder" else "plain-token",
    )

    runtime = await load_vision_runtime_config(object())  # type: ignore[arg-type]

    assert runtime == VisionRuntimeConfig(
        x_token="plain-token",
        profile_id="profile-1",
        folder_id="folder-1",
        configuration_revision="2026-07-28T00:00:00+00:00",
    )
    assert "plain-token" not in repr(runtime)
    assert "folder-1" not in repr(runtime)


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "message"),
    [
        (None, "Vision is not configured in PostgreSQL"),
        (_row(token=""), "Vision token is not configured in PostgreSQL"),
        (_row(profile_id="  "), "Vision profile is not configured in PostgreSQL"),
    ],
)
async def test_load_vision_runtime_config_fails_closed_for_incomplete_row(
    monkeypatch: pytest.MonkeyPatch,
    row: object | None,
    message: str,
) -> None:
    import core.vision_runtime as module

    monkeypatch.setattr(module, "AsyncSession", lambda _engine: _Session(row))

    with pytest.raises(VisionConfigurationError, match=f"^{message}$"):
        await load_vision_runtime_config(object())  # type: ignore[arg-type]


@pytest.mark.asyncio
async def test_load_vision_runtime_config_hides_decrypt_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import core.vision_runtime as module

    monkeypatch.setattr(module, "AsyncSession", lambda _engine: _Session(_row()))

    def fail_decrypt(_token: str) -> str:
        raise ValueError("secret ciphertext details")

    monkeypatch.setattr(module, "decrypt", fail_decrypt)

    with pytest.raises(
        VisionConfigurationError,
        match="^Vision token cannot be decrypted$",
    ) as exc_info:
        await load_vision_runtime_config(object())  # type: ignore[arg-type]

    assert "secret ciphertext details" not in str(exc_info.value)
