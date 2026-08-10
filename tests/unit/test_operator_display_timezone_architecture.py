"""Static contracts for the server-authoritative operator display timezone."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def _source(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def test_clients_have_no_device_or_local_storage_timezone_authority() -> None:
    display_paths = (
        "frontend/src/lib/timezone.ts",
        "frontend/src/components/settings/DisplayTab.tsx",
        "frontend-mini/src/features/settings/DisplaySettings.tsx",
    )
    source = "\n".join(_source(path) for path in display_paths)
    ui_store = _source("frontend/src/stores/ui.ts")

    assert "fb-agent-mini-display-timezone" not in source
    assert "resolveDisplayTimeZone" not in source
    assert "localStorage" not in source
    assert "isValidIanaTimeZone" not in source
    assert source.count("isOperatorDisplayTimezoneCandidate") >= 2
    assert "displayTimeZone:" not in ui_store
    assert "fb-agent-mini-display-timezone" not in ui_store


def test_analytics_uses_server_preference_only_for_presentation() -> None:
    for relative in (
        "frontend/src/routes/analytics/index.tsx",
        "frontend-mini/src/routes/analytics/index.tsx",
    ):
        source = _source(relative)
        assert "useOperatorDisplayPreference" in source
        assert "scope.display_timezone" not in source
        performance_params = source.split("const performanceParams", maxsplit=1)[1].split(
            "};", maxsplit=1
        )[0]
        assert "timezone" not in performance_params.lower()
