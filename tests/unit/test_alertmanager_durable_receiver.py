from __future__ import annotations

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def test_alertmanager_receiver_targets_only_durable_notification_plane() -> None:
    config = yaml.safe_load(
        (ROOT / "deploy/monitoring/alertmanager/alertmanager.yml").read_text(encoding="utf-8")
    )
    receiver = next(
        item for item in config["receivers"] if item["name"] == "durable-notification-plane"
    )
    webhook = receiver["webhook_configs"][0]

    assert config["route"]["receiver"] == "durable-notification-plane"
    assert webhook["send_resolved"] is True
    assert webhook["max_alerts"] == 100
    assert webhook["url"].endswith("/api/v1/integrations/alertmanager/webhook")
    assert webhook["http_config"]["authorization"] == {
        "type": "Bearer",
        "credentials_file": "/run/secrets/alertmanager_webhook_token",
    }
    rendered = str(config).lower()
    assert "api.telegram.org" not in rendered
    assert "bot_token" not in rendered


def test_alertmanager_secret_is_a_required_read_only_mount() -> None:
    compose = (ROOT / "deploy/monitoring/docker-compose.monitoring.yml").read_text(encoding="utf-8")
    assert "ALERTMANAGER_WEBHOOK_TOKEN_FILE:?" in compose
    assert "/run/secrets/alertmanager_webhook_token:ro" in compose
