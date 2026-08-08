from __future__ import annotations

import inspect
from pathlib import Path

import apps.observer_worker.main as observer
import core.meta_api.account_tz as account_tz

ROOT = Path(__file__).resolve().parents[2]


def test_production_loop_never_runs_an_unclaimed_browser_scan() -> None:
    loop_source = inspect.getsource(observer.main_loop)
    claimed_source = inspect.getsource(observer._run_claimed_observer_scan)

    assert "run_one_cycle(" not in loop_source
    assert "enqueue_scheduled_observer_scan(" in loop_source
    assert "claim_observer_scan(" in loop_source
    assert "run_one_cycle(" in claimed_source
    assert "run_with_observer_scan_control(" in claimed_source


def test_every_direct_api_or_watchdog_browser_read_has_a_durable_fence() -> None:
    fenced_readers = (
        "apps/api/routers/v1/campaigns_meta.py",
        "apps/api/routers/v1/adset_duplicates.py",
        "apps/api/routers/v1/settings_vision.py",
        "apps/health_watchdog/main.py",
    )
    for relative in fenced_readers:
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "BrowserOperationFence" in source, relative

    observer_settings = (ROOT / "apps/api/routers/v1/settings_observer.py").read_text(
        encoding="utf-8"
    )
    refresh = observer_settings[observer_settings.index("async def refresh_observer_campaigns") :]
    assert "BrowserExclusiveMaintenance" in refresh
    assert refresh.index("BrowserExclusiveMaintenance") < refresh.index(
        "await client.start_browser()"
    )

    registry = (ROOT / "core/ai_assistant/tools/registry.py").read_text(encoding="utf-8")
    registry_without_layout = "".join(registry.split())
    assert (
        'tool.__class__.__module__.startswith("core.ai_assistant.tools.meta.")'
        in registry_without_layout
    )
    assert 'operation_kind="ai_meta_read"' in registry

    assert not (ROOT / "scripts/start_vision_session.py").exists()


def test_account_context_refresh_holds_fence_through_persistence_boundary() -> None:
    refresh = inspect.getsource(account_tz.refresh_account_timezones)

    assert 'operation_kind="account_context_refresh"' in refresh
    assert refresh.index("async with BrowserOperationFence(") < refresh.index(
        "await fetch_account_context("
    )
    assert refresh.index("await fetch_account_context(") < refresh.index(
        "await fence.assert_held()"
    )
    assert refresh.index("await fence.assert_held()") < refresh.index(
        "await persist_account_context("
    )


def test_automatic_network_recovery_cannot_reconnect_or_restart_vision() -> None:
    manager = (ROOT / "services/browser-agent/src/session-manager.ts").read_text(encoding="utf-8")
    recovery = manager[
        manager.index("async reloadPageAfterNetworkFailure") : manager.index(
            "\n  getSession(", manager.index("async reloadPageAfterNetworkFailure")
        )
    ]
    reload_helper = manager[
        manager.index("async function reloadPageWithinOperation") : manager.index(
            "\nfunction buildMaintenanceRecoveryRequiredError",
            manager.index("async function reloadPageWithinOperation"),
        )
    ]
    assert "reloadPageWithinOperation" in recovery
    assert "page.reload" in reload_helper
    assert "reconnectBrowser" not in recovery
    assert "forceProfileRestart" not in recovery
    assert "restart_profile" not in recovery
    assert "reconnectBrowser" not in reload_helper
    assert "forceProfileRestart" not in reload_helper
    assert "restart_profile" not in reload_helper

    for relative in (
        "services/browser-agent/src/index.ts",
        "services/browser-agent/src/meta-api/service.ts",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert "reloadPageAfterNetworkFailure" in source
        assert "healSessionNetwork" not in source


def test_force_profile_recovery_is_maintenance_only_and_reprobed() -> None:
    api = (ROOT / "apps/api/routers/v1/settings_vision.py").read_text(encoding="utf-8")
    ensure = api[
        api.index("async def post_vision_ensure_cdp") : api.index(
            "\n# ---------------------------------------------------------------------------\n# Экспорт",
            api.index("async def post_vision_ensure_cdp"),
        )
    ]
    assert "BrowserMaintenanceGuard" in ensure
    assert "await guard.assert_held()" in ensure
    assert "_recover_browser_profile_under_maintenance" in ensure
    assert ensure.index("await guard.assert_held()") < ensure.index(
        "_recover_browser_profile_under_maintenance"
    )
    assert ensure.index("_recover_browser_profile_under_maintenance") < ensure.rindex(
        "_probe_browser_channel"
    )
    assert "maintenance_owner=request.headers.get(" in api
    assert "BrowserMaintenanceGuard(engine, maintenance_owner)" in api

    healer = (ROOT / "scripts/platform-desktop-heal.sh").read_text(encoding="utf-8")
    assert "X-FB-Agent-Browser-Maintenance-Owner: $BROWSER_MAINTENANCE_OWNER" in healer

    client = (ROOT / "clients/python_grpc/client.py").read_text(encoding="utf-8")
    assert "RecoverBrowserProfileUnderMaintenance" in client
    assert "maintenance_owner" in client

    browser_agent = (ROOT / "services/browser-agent/src/index.ts").read_text(encoding="utf-8")
    recovery = browser_agent[
        browser_agent.index(
            "async function recoverBrowserProfileUnderMaintenance"
        ) : browser_agent.index(
            "\nasync function",
            browser_agent.index("async function recoverBrowserProfileUnderMaintenance") + 20,
        )
    ]
    assert "PERMISSION_DENIED" in recovery
    assert "recoverBrowserProfileUnderMaintenance" in recovery


def test_retired_browser_lifecycle_fallbacks_are_absent() -> None:
    proto = (ROOT / "proto/v1/browser_session.proto").read_text(encoding="utf-8")
    client = (ROOT / "clients/python_grpc/client.py").read_text(encoding="utf-8")
    observer_source = (ROOT / "apps/observer_worker/main.py").read_text(encoding="utf-8")
    model = (ROOT / "core/models/settings/vision_config.py").read_text(encoding="utf-8")
    baseline = (ROOT / "migrations/versions/0001_safety_first_baseline.sql").read_text(
        encoding="utf-8"
    )

    for retired_rpc in (
        "StopBrowser",
        "DisconnectBrowser",
        "Navigate",
        "GetSessionInfo",
        "StreamSessionStatus",
    ):
        assert retired_rpc not in proto
        assert retired_rpc not in client
    for retired_flag in (
        "auto_restart_on_missing_cdp",
        "VISION_AUTO_RESTART_ON_MISSING_CDP",
        "auto_recover_page",
    ):
        assert retired_flag not in client
        assert retired_flag not in observer_source
        assert retired_flag not in model
        assert retired_flag not in baseline

    browser_agent = (ROOT / "services/browser-agent/src/index.ts").read_text(encoding="utf-8")
    list_campaigns = browser_agent[
        browser_agent.index("async function listCampaignsHandler") : browser_agent.index(
            "\n// --- Запуск сервера ---"
        )
    ]
    assert "sessionManager.getSession(sessionId)" in list_campaigns
    assert "getPreferredSession" not in list_campaigns
