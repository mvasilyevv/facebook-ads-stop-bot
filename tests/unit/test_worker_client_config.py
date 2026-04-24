# -*- coding: utf-8 -*-
"""Тесты конфигурации gRPC-клиентов воркеров."""

from __future__ import annotations

from types import SimpleNamespace


# Проверяем, что disable worker собирает совместимую конфигурацию без устаревшего initial_url.
def test_disable_worker_build_client_config_uses_current_browser_agent_fields(monkeypatch):
    import run_disable_worker

    monkeypatch.setattr(
        run_disable_worker,
        "get_settings",
        lambda: SimpleNamespace(vision_folder_id="folder-1"),
    )

    config = run_disable_worker._build_client_config("token", "http://vision.local", "profile-1")

    assert config.vision_x_token == "token"
    assert config.vision_api_url == "http://vision.local"
    assert config.vision_profile_id == "profile-1"
    assert config.vision_folder_id == "folder-1"
    assert not hasattr(config, "initial_url")


# Проверяем, что enable worker тоже не передаёт устаревший initial_url в BrowserAgentConfig.
def test_enable_worker_build_client_config_uses_current_browser_agent_fields(monkeypatch):
    import run_enable_worker

    monkeypatch.setattr(
        run_enable_worker,
        "get_settings",
        lambda: SimpleNamespace(vision_folder_id="folder-2"),
    )

    config = run_enable_worker._build_client_config("token-2", "http://vision.other", "profile-2")

    assert config.vision_x_token == "token-2"
    assert config.vision_api_url == "http://vision.other"
    assert config.vision_profile_id == "profile-2"
    assert config.vision_folder_id == "folder-2"
    assert not hasattr(config, "initial_url")
