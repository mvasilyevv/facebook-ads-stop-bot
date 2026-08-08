from __future__ import annotations

import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
ROOT_COMPOSE = ROOT / "docker-compose.yml"
PRODUCTION_COMPOSE = ROOT / "deploy/compose/docker-compose.app.yml"
PRODUCTION_DESKTOP_COMPOSE = ROOT / "deploy/compose/docker-compose.desktop-agent.yml"

LOCAL_SAFE_SERVICES = {
    "postgres",
    "redis",
    "migrate",
    "api",
    "telegram_delivery_worker",
    "telegram_update_worker",
}


def test_legacy_host_runtime_is_physically_absent() -> None:
    forbidden_paths = (
        ROOT / "run.sh",
        ROOT / "supervisord.conf",
        ROOT / "bin/supervisor_crashmail.py",
    )

    assert not [str(path.relative_to(ROOT)) for path in forbidden_paths if path.exists()]

    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = project["project"]["dependencies"]
    assert not [dependency for dependency in dependencies if dependency.startswith("supervisor")]


def test_root_compose_contains_only_fail_closed_local_services() -> None:
    document = yaml.safe_load(ROOT_COMPOSE.read_text(encoding="utf-8"))
    services = document["services"]

    assert set(services) == LOCAL_SAFE_SERVICES
    assert "networks" not in document
    assert services["postgres"]["environment"]["POSTGRES_DB"] == ("${POSTGRES_DB:-fb_stop_bot_dev}")

    for service_name in {
        "migrate",
        "api",
        "telegram_delivery_worker",
        "telegram_update_worker",
    }:
        assert services[service_name]["environment"]["FB_AGENT_PROFILE"] == "local"

    api_environment = services["api"]["environment"]
    assert not [key for key in api_environment if key.startswith("BROWSER_AGENT")]
    assert services["migrate"]["environment"]["WORKER_TYPE"] == "migrate"
    assert services["telegram_delivery_worker"]["environment"]["WORKER_TYPE"] == (
        "telegram_delivery"
    )
    assert services["telegram_update_worker"]["environment"]["WORKER_TYPE"] == ("telegram_updates")
    for service_name in {
        "api",
        "telegram_delivery_worker",
        "telegram_update_worker",
    }:
        assert "redis" not in services[service_name].get("depends_on", {})
    local_environment = (ROOT / ".env.local.example").read_text(encoding="utf-8")
    assert "\nFB_AGENT_PROFILE=local\n" in local_environment
    assert "\nPOSTGRES_DB=fb_stop_bot_dev\n" in local_environment
    assert "\nPOSTGRES_DB=fb_stop_bot\n" not in local_environment


def test_money_workers_exist_only_in_production_compose() -> None:
    local_services = yaml.safe_load(ROOT_COMPOSE.read_text(encoding="utf-8"))["services"]
    production_services = yaml.safe_load(PRODUCTION_COMPOSE.read_text(encoding="utf-8"))["services"]

    money_services = {
        "observer",
        "autopause_worker",
        "meta_api",
        "cabinet_scheduler",
        "campaign_creator",
    }
    assert money_services.isdisjoint(local_services)
    assert money_services.issubset(production_services)
    desktop_services = yaml.safe_load(PRODUCTION_DESKTOP_COMPOSE.read_text(encoding="utf-8"))[
        "services"
    ]
    assert "browser-agent" not in local_services
    assert set(desktop_services) == {"browser-agent"}
    assert (
        production_services["autopause_worker"]["environment"]["META_API_WORKER_LANES"] == "money"
    )
    assert (
        production_services["meta_api"]["environment"]["META_API_WORKER_LANES"]
        == "interactive,bulk,background"
    )


def test_worker_metrics_document_describes_container_only_runtime() -> None:
    document = (ROOT / "docs/worker_metrics.md").read_text(encoding="utf-8")

    assert "deploy/compose/" in document
    assert "WORKER_METRICS_PORT=9464" in document
    assert "FB_AGENT_PROFILE=local" in document
    assert "supervisord" not in document
