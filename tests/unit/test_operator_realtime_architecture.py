from __future__ import annotations

from pathlib import Path

from apps.api.main import create_app

ROOT = Path(__file__).resolve().parents[2]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_operator_websocket_is_the_only_dashboard_realtime_route() -> None:
    source = _source("apps/api/routers/ws.py")

    assert '@router.websocket("/ws/operator")' in source
    assert "/ws/dashboard" not in source
    assert "redis.asyncio" not in source


def test_retired_dashboard_pubsub_channels_cannot_return() -> None:
    sources = "\n".join(
        _source(relative)
        for relative in (
            "core/pubsub.py",
            "apps/observer_worker/main.py",
            "apps/meta_api_worker/main.py",
            "apps/health_watchdog/main.py",
            "apps/tracker_reconciliation_worker/main.py",
        )
    )

    for retired in (
        "CHANNEL_SCAN_FINISHED",
        "CHANNEL_TASK_CHANGED",
        "CHANNEL_HEALTH_UPDATED",
        "CHANNEL_TRACKER_CHANGED",
        "CHANNEL_ALERT_CREATED",
        "fb_agent:scan:finished",
        "fb_agent:task:changed",
        "fb_agent:health:updated",
        "fb_agent:tracker:changed",
        "fb_agent:alert:created",
    ):
        assert retired not in sources

    pubsub = _source("core/pubsub.py")
    assert "CHANNEL_TRACKER_WAKEUP" in pubsub
    assert "CHANNEL_OBSERVER_TRIGGER" not in pubsub
    assert "fb_agent:observer:trigger" not in sources


def test_legacy_http_surfaces_are_physically_absent() -> None:
    retired_modules = (
        "apps/api/routers/v1/dashboard.py",
        "apps/api/routers/v1/dashboard_stats.py",
        "apps/api/routers/v1/dashboard_timeseries.py",
        "apps/api/routers/v1/dashboard_performance.py",
        "apps/api/routers/v1/ads_actions.py",
        "apps/api/routers/v1/ads_admin.py",
        "apps/api/routers/v1/ads_timeline.py",
        "apps/api/routers/v1/auto_enable.py",
        "apps/api/routers/v1/disable_tasks.py",
        "apps/api/routers/v1/enable_tasks.py",
        "apps/api/routers/v1/enable_recommendations.py",
        "apps/api/routers/v1/history.py",
        "apps/api/routers/v1/schemas/ads_actions.py",
        "apps/api/routers/v1/schemas/ads_admin.py",
        "apps/api/routers/v1/schemas/ads_timeline.py",
        "apps/api/routers/v1/schemas/auto_enable.py",
        "apps/api/routers/v1/schemas/dashboard.py",
        "apps/api/routers/v1/schemas/dashboard_aggregates.py",
        "apps/api/routers/v1/schemas/tasks.py",
        "apps/api/routers/v1/schemas/history.py",
        "apps/api/routers/v1/schemas/tma.py",
        "apps/api/utils/alert_serializer.py",
        "apps/api/utils/task_serializer.py",
        "core/dashboard/snapshot.py",
        "core/dashboard/history_queries.py",
        "core/tasks/bulk_disable.py",
    )
    for relative in retired_modules:
        assert not (ROOT / relative).exists(), relative


def test_runtime_routes_expose_only_canonical_operator_and_settings_contracts() -> None:
    paths = create_app().openapi()["paths"]

    assert not any(path.startswith("/api/dashboard") for path in paths)
    assert not any(path.startswith("/api/history") for path in paths)
    assert not any(path.startswith("/api/tma/ads") for path in paths)
    assert not any(path.startswith("/api/ads/") and path.endswith("/timeline") for path in paths)

    assert set(paths["/api/operator/events"]) >= {"get"}
    assert not any("auto-enable" in path for path in paths)

    assert not any(path.startswith("/api/stats") for path in paths)
    assert "/api/operator/snapshot" in paths
    assert "/api/tma/auth" in paths
    assert "/api/tma/me" in paths
    assert "/api/tma/navigation/resolve" in paths
    assert "/api/tma/cabinet-autostart" not in paths
    assert "/api/settings/cabinet-autostart" not in paths
