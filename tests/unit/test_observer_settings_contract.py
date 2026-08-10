"""Observer settings expose only field-scoped writes."""

from apps.api.main import create_app


def test_observer_settings_forbid_full_snapshot_put() -> None:
    paths = create_app().openapi()["paths"]

    assert "put" not in paths["/api/settings/observer"]
    assert "patch" in paths["/api/settings/observer/interval"]
    assert "patch" in paths["/api/settings/observer/scanning"]
    assert "patch" in paths["/api/settings/observer/owner-tag"]
    assert "patch" in paths["/api/settings/observer/campaigns"]
