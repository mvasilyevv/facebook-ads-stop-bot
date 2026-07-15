from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    path = ROOT / "deploy" / "vision-refresh-token.py"
    spec = importlib.util.spec_from_file_location("vision_refresh_token_test", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_update_env_token_is_atomic_durable_and_keeps_backup(tmp_path, monkeypatch) -> None:
    module = _load_module()
    env_path = tmp_path / ".env"
    original = "API_KEY=keep-me\nVISION_X_TOKEN=old\nPOSTGRES_PASSWORD=also-keep\n"
    env_path.write_text(original)
    env_path.chmod(0o640)
    monkeypatch.setattr(module.time, "time", lambda: 1234)
    fsync_calls: list[int] = []
    real_fsync = module.os.fsync

    def record_fsync(fd: int) -> None:
        fsync_calls.append(fd)
        real_fsync(fd)

    monkeypatch.setattr(module.os, "fsync", record_fsync)

    module.update_env_token(str(env_path), "new-token")

    assert env_path.read_text() == original.replace("old", "new-token")
    assert (tmp_path / ".env.bak.token-1234").read_text() == original
    assert os.stat(env_path).st_mode & 0o777 == 0o600
    assert len(fsync_calls) == 2  # temp file, then containing directory
    assert not list(tmp_path.glob("..env.token-*"))


def test_update_env_token_leaves_live_file_intact_if_replace_fails(tmp_path, monkeypatch) -> None:
    module = _load_module()
    env_path = tmp_path / ".env"
    original = "VISION_X_TOKEN=old\nOTHER=value\n"
    env_path.write_text(original)
    monkeypatch.setattr(module.time, "time", lambda: 99)

    def fail_replace(_source: str, _destination: str) -> None:
        raise OSError("simulated replace failure")

    monkeypatch.setattr(module.os, "replace", fail_replace)

    with pytest.raises(OSError, match="simulated replace failure"):
        module.update_env_token(str(env_path), "new")

    assert env_path.read_text() == original
    assert (tmp_path / ".env.bak.token-99").read_text() == original
    assert not list(tmp_path.glob("..env.token-*"))


def test_recreate_runtime_restarts_every_static_vision_token_consumer(monkeypatch) -> None:
    module = _load_module()
    module.FB_AGENT_ROOT = "/srv/fb-agent"
    calls: list[tuple[list[str], dict[str, object]]] = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    module.recreate_runtime()

    assert len(calls) == 1
    command, kwargs = calls[0]
    assert command[:6] == [
        "/srv/fb-agent/current/scripts/server-compose.sh",
        "compose",
        "up",
        "-d",
        "--no-deps",
        "--force-recreate",
    ]
    services = set(command[6:])
    assert services == {
        "browser-agent",
        "api",
        "observer",
        "meta_api",
        "health_watchdog",
        "campaign_creator",
        "creator_worker",
        "creator_recorder",
    }
    assert kwargs["check"] is True
    assert kwargs["env"]["FB_AGENT_ROOT"] == "/srv/fb-agent"
