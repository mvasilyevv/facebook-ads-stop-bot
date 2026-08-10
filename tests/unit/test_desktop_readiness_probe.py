from __future__ import annotations

import asyncio
from pathlib import Path

import httpx
import pytest

from apps.api.routers import desktop_auth
from apps.api.routers.desktop_auth import DesktopReadyzCache, NetworkDesktopReadinessProbe
from core.config import Settings


def _settings(*, credential_path: Path | None = None, ttl: float = 0) -> Settings:
    return Settings(
        _env_file=None,
        require_api_key=False,
        desktop_kasm_internal_url="http://vision-webtop:8444/",
        desktop_readiness_credentials_path=str(
            credential_path or Path("/missing/desktop-readiness.env")
        ),
        desktop_readiness_timeout_seconds=1.0,
        desktop_readiness_cache_seconds=ttl,
    )


def _write_credentials(root: Path, *, state: str, password: str) -> Path:
    states = root / "states"
    states.mkdir(parents=True, exist_ok=True)
    destination = states / f"{state}.env"
    destination.write_text(
        f"DESKTOP_KASM_SERVICE_USER=adpulse-desktop\nDESKTOP_KASM_SERVICE_PASSWORD={password}\n",
        encoding="utf-8",
    )
    destination.chmod(0o600)
    active = root / "active.env"
    active.unlink(missing_ok=True)
    active.symlink_to(f"states/{state}.env")
    return active


@pytest.mark.asyncio
async def test_kasm_probe_requires_auth_challenge_and_authenticated_surface(
    monkeypatch,
    tmp_path: Path,
):
    calls: list[object] = []

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

        async def get(self, url, *, auth=None):
            calls.append((url, auth))
            return httpx.Response(401 if auth is None else 200)

    monkeypatch.setattr(desktop_auth.httpx, "AsyncClient", lambda **_kwargs: Client())

    credentials = _write_credentials(
        tmp_path / "desktop-readiness",
        state="release-a",
        password="service-password-a",
    )
    checks = await NetworkDesktopReadinessProbe().check(_settings(credential_path=credentials))

    assert checks == {"configured": True, "auth_challenge": True, "authenticated": True}
    assert len(calls) == 2
    assert calls[0][1] is None
    assert isinstance(calls[1][1], httpx.BasicAuth)


@pytest.mark.asyncio
async def test_kasm_probe_fails_closed_without_service_credentials(tmp_path: Path):
    credentials = _write_credentials(
        tmp_path / "desktop-readiness",
        state="release-a",
        password="",
    )
    checks = await NetworkDesktopReadinessProbe().check(_settings(credential_path=credentials))
    assert checks == {"configured": False, "auth_challenge": False, "authenticated": False}


class _CountingProbe:
    def __init__(self, *results: dict[str, bool]) -> None:
        self.calls = 0
        self._results = list(results)
        self.release: asyncio.Event | None = None

    async def check(self, settings: Settings) -> dict[str, bool]:
        del settings
        self.calls += 1
        if self.release is not None:
            await self.release.wait()
        return self._results[min(self.calls, len(self._results)) - 1]


@pytest.mark.asyncio
async def test_readyz_cache_serves_positive_and_negative_results_until_ttl():
    clock = {"now": 100.0}
    probe = _CountingProbe(
        {"configured": True, "auth_challenge": False, "authenticated": False},
        {"configured": True, "auth_challenge": True, "authenticated": True},
    )
    cache = DesktopReadyzCache(monotonic=lambda: clock["now"])
    settings = _settings(ttl=15.0)

    first = await cache.get(settings, probe)
    second = await cache.get(settings, probe)
    clock["now"] += 15.0
    refreshed = await cache.get(settings, probe)

    assert probe.calls == 2
    assert first == second
    assert refreshed["authenticated"] is True


@pytest.mark.asyncio
async def test_readyz_cache_can_be_disabled_and_serializes_concurrent_probes():
    disabled_probe = _CountingProbe({"configured": True})
    disabled_cache = DesktopReadyzCache(monotonic=lambda: 100.0)
    await disabled_cache.get(_settings(ttl=0), disabled_probe)
    await disabled_cache.get(_settings(ttl=0), disabled_probe)
    assert disabled_probe.calls == 2

    probe = _CountingProbe({"configured": True, "authenticated": True})
    probe.release = asyncio.Event()
    cache = DesktopReadyzCache(monotonic=lambda: 100.0)
    tasks = [asyncio.create_task(cache.get(_settings(ttl=15), probe)) for _ in range(5)]
    await asyncio.sleep(0)
    probe.release.set()
    results = await asyncio.gather(*tasks)
    assert probe.calls == 1
    assert all(result == {"configured": True, "authenticated": True} for result in results)


@pytest.mark.asyncio
async def test_readyz_cache_invalidates_when_committed_credentials_rotate(tmp_path: Path):
    root = tmp_path / "desktop-readiness"
    active = _write_credentials(
        root,
        state="release-a",
        password="service-password-a",
    )
    settings = _settings(credential_path=active, ttl=60)
    probe = _CountingProbe(
        {"configured": True, "authenticated": True},
        {"configured": True, "authenticated": False},
    )
    cache = DesktopReadyzCache(monotonic=lambda: 100.0)

    first = await cache.get(settings, probe)
    _write_credentials(
        root,
        state="release-b",
        password="service-password-b",
    )
    rotated = await cache.get(settings, probe)

    assert probe.calls == 2
    assert first["authenticated"] is True
    assert rotated["authenticated"] is False
