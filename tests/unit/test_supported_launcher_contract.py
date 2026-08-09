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
    assert (
        production_services["observer"]["environment"]["OBSERVER_CABINET_CONCURRENCY"]
        == "${OBSERVER_CABINET_CONCURRENCY:-2}"
    )
    observer_source = (ROOT / "apps/observer_worker/main.py").read_text(encoding="utf-8")
    assert 'os.environ.get("OBSERVER_CABINET_CONCURRENCY", "2")' in observer_source


def test_forbidden_auto_activate_runtime_is_physically_absent() -> None:
    forbidden_paths = (
        "apps/cabinet_scheduler",
        "apps/enable_recommendation_worker",
        "run_cabinet_scheduler.py",
        "run_enable_recommendation_worker.py",
        "apps/api/routers/v1/settings_cabinet_autostart.py",
        "apps/api/routers/v1/schemas/cabinet_autostart.py",
        "core/scheduler/cabinet_autostart.py",
        "core/enable_reco/__init__.py",
        "core/enable_reco/analyzer.py",
        "core/enable_reco/confirmation.py",
        "core/models/observer/ad_auto_enable_disabled.py",
        "core/models/tasks/enable_recommendation.py",
    )

    assert not [relative for relative in forbidden_paths if (ROOT / relative).exists()]

    runtime_sources = (
        "deploy/compose/docker-compose.app.yml",
        "scripts/bluegreen-worker-handoff.sh",
        "scripts/platform-compose.sh",
        "scripts/reconcile-platform-release.sh",
        "scripts/platform-network-inventory.py",
        "docker/worker-entrypoint.sh",
        "docker/Dockerfile.workers",
        "apps/api/routers/v1/settings_observer.py",
        "apps/api/routers/v1/schemas/settings_observer.py",
        "apps/meta_api_worker/main.py",
        "core/commands/service.py",
        "core/meta_api/bulk.py",
        "core/meta_api/client.py",
        "core/models/settings/observer_config.py",
        "migrations/versions/0001_safety_first_baseline.sql",
    )
    forbidden_contracts = (
        "cabinet_autostart",
        "cabinet_scheduler",
        "enable_recommendation_worker",
        "auto_enable_recommendations",
        "enable_recommendations",
        "ad_auto_enable_disabled",
        "/auto-enable",
        "activation_guards",
        "autostart_reconciliation",
        "bulk_execution_ad_ids",
        "bulk_guard_rejected",
        "guarded_autostart",
        "locked_autostart",
        "supersedes_autostart_task_id",
    )
    combined = "\n".join(
        (ROOT / relative).read_text(encoding="utf-8") for relative in runtime_sources
    )
    assert not [contract for contract in forbidden_contracts if contract in combined]


def test_worker_metrics_document_describes_container_only_runtime() -> None:
    document = (ROOT / "docs/worker_metrics.md").read_text(encoding="utf-8")

    assert "deploy/compose/" in document
    assert "WORKER_METRICS_PORT=9464" in document
    assert "FB_AGENT_PROFILE=local" in document
    assert "supervisord" not in document
