from __future__ import annotations

import ast
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]


def _calls_metrics_server(path: Path, seen: set[Path] | None = None) -> bool:
    seen = set() if seen is None else seen
    path = path.resolve()
    if path in seen or not path.is_file():
        return False
    seen.add(path)
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    if any(
        isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "start_worker_metrics_server"
        for node in ast.walk(tree)
    ):
        return True
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or not node.module:
            continue
        candidate = ROOT / (node.module.replace(".", "/") + ".py")
        package = ROOT / node.module.replace(".", "/") / "__init__.py"
        if _calls_metrics_server(candidate if candidate.is_file() else package, seen):
            return True
    return False


def _worker_type_entrypoints() -> dict[str, Path]:
    entrypoint = (ROOT / "docker/worker-entrypoint.sh").read_text(encoding="utf-8")
    return {
        worker_type: ROOT / command.removeprefix("./")
        for worker_type, command in re.findall(
            r"^  ([a-z_]+)\)\n(?:    .*\n)*?    exec python ([A-Za-z0-9_./-]+)$",
            entrypoint,
            flags=re.MULTILINE,
        )
        if not command.startswith("-m")
    }


def _actual_entrypoint(service: dict, worker_entrypoints: dict[str, Path]) -> Path:
    command = service.get("entrypoint")
    if command:
        assert command[:2] == ["python", "-m"]
        return ROOT / (command[2].replace(".", "/") + ".py")
    worker_type = service.get("environment", {}).get("WORKER_TYPE")
    assert worker_type in worker_entrypoints
    return worker_entrypoints[worker_type]


def test_every_labeled_python_worker_has_a_real_metrics_entrypoint() -> None:
    compose = yaml.safe_load(
        (ROOT / "deploy/compose/docker-compose.app.yml").read_text(encoding="utf-8")
    )
    labeled_workers = {
        name
        for name, service in compose["services"].items()
        if "workers" in service.get("profiles", [])
        and service.get("labels", {}).get("com.fb-agent.metrics") == "true"
    }
    worker_entrypoints = _worker_type_entrypoints()
    uncovered = {
        service
        for service in labeled_workers
        if not _calls_metrics_server(
            _actual_entrypoint(compose["services"][service], worker_entrypoints)
        )
    }
    assert uncovered == set()


def test_alerts_fail_unknown_when_expected_series_disappear() -> None:
    rules = (ROOT / "deploy/monitoring/prometheus/rules/fb-agent.yml").read_text()
    compose = yaml.safe_load(
        (ROOT / "deploy/compose/docker-compose.app.yml").read_text(encoding="utf-8")
    )
    expected_heartbeats = {
        "observer",
        "autopause",
        "meta_api",
        "telegram_delivery",
        "telegram_updates",
        "cleanup",
        "reconciler",
        "health_watchdog",
        "digest_scheduler",
        "tracker_reconciliation_worker",
        "campaign_creator",
    }
    labeled_workers = {
        name
        for name, service in compose["services"].items()
        if "workers" in service.get("profiles", [])
        and service.get("labels", {}).get("com.fb-agent.metrics") == "true"
    }

    assert len(labeled_workers) == len(expected_heartbeats)
    assert "or vector(0)" not in rules
    assert 'absent(probe_success{job="blackbox-http",probe="healthz"})' in rules
    assert 'absent(probe_success{job="blackbox-http",probe="readyz"})' in rules
    assert "FBMoneyQueueMetricsAbsent" in rules
    assert "FBSnapshotMetricAbsent" in rules
    assert "FBNotificationMetricsAbsent" in rules
    for worker in expected_heartbeats:
        assert f'absent(fb_agent_worker_heartbeat_timestamp_seconds{{worker="{worker}"}})' in rules


def test_browser_agent_metrics_use_the_shared_vision_network_target() -> None:
    desktop = yaml.safe_load(
        (ROOT / "deploy/compose/docker-compose.desktop-agent.yml").read_text(encoding="utf-8")
    )
    labels = desktop["services"]["browser-agent"]["labels"]
    agent = (ROOT / "deploy/monitoring/alloy/agent.alloy").read_text(encoding="utf-8")
    local = (ROOT / "deploy/monitoring/alloy/browser-agent-local.alloy").read_text(encoding="utf-8")

    assert labels["com.fb-agent.metrics"] == "true"
    assert labels["com.fb-agent.metrics.address"] == "vision-webtop"
    assert labels["com.fb-agent.metrics.port"] == "9464"
    assert '"vision-webtop:9464"' in agent
    assert '"vision-webtop:9464"' in local


def test_observer_snapshot_age_is_exported_without_redis_liveness() -> None:
    observer = (ROOT / "apps/observer_worker/main.py").read_text(encoding="utf-8")
    metrics = observer[
        observer.index("async def metrics_loop(") : observer.index(
            "# ====================== One cycle", observer.index("async def metrics_loop(")
        )
    ]
    startup = observer[
        observer.index("state = _ObserverState()") : observer.index(
            'logger.info("observer_worker запущен")'
        )
    ]

    assert 'SNAPSHOT_AGE.labels(source="observer_ads")' in metrics
    assert 'float("inf") if snapshot_age is None' in metrics
    assert "metrics_task = asyncio.create_task(" in startup
    assert "metrics_loop(" in startup
    assert "redis" not in startup.lower()
