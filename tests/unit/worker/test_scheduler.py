from __future__ import annotations

from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from apps.worker.scheduler import SchedulerService


@dataclass(slots=True)
class _FakeProfile:
    vendor_profile_id: str


@dataclass(slots=True)
class _FakeBrowserHost:
    name: str


@dataclass(slots=True)
class _FakeActiveProfileRecord:
    profile: _FakeProfile
    browser_host: _FakeBrowserHost


class _FakeSessionFactory:
    def __init__(self, session) -> None:
        self._session = session

    def __call__(self):
        return self

    async def __aenter__(self):
        return self._session

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None


class _FakeRedis:
    """Заглушка Redis для тестирования планировщика с распределёнными блокировками."""

    def __init__(self) -> None:
        self._store: dict[str, str] = {}

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self._store:
            return False
        self._store[key] = value
        return True

    async def delete(self, key: str) -> int:
        return 1 if self._store.pop(key, None) is not None else 0


class _FakeScanService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def run_once(self, *, profile_id: str, browser_host_name: str):
        self.calls.append((profile_id, browser_host_name))
        return SimpleNamespace(rows_parsed=3, scan_run_id="scan-run-1")


# Проверяет, что scheduler не пытается запускать scanner, если в базе нет активных профилей.
@pytest.mark.asyncio
async def test_scheduler_cycle_skips_when_no_active_profiles(monkeypatch) -> None:
    scheduler = object.__new__(SchedulerService)
    scheduler._settings = SimpleNamespace(worker_scan_interval_seconds=120)
    scheduler._session_factory = _FakeSessionFactory(object())
    scheduler._scan_service = _FakeScanService()
    scheduler._redis = _FakeRedis()

    class _EmptyBrowserRepository:
        def __init__(self, session) -> None:
            self.session = session

        async def list_active_profiles(self):
            return []

    monkeypatch.setattr("apps.worker.scheduler.BrowserRepository", _EmptyBrowserRepository)

    await scheduler._run_cycle()

    assert scheduler._scan_service.calls == []


# Проверяет, что scheduler запускает реальный scan service для каждого активного профиля из базы.
@pytest.mark.asyncio
async def test_scheduler_cycle_scans_all_active_profiles(monkeypatch) -> None:
    scheduler = object.__new__(SchedulerService)
    scheduler._settings = SimpleNamespace(worker_scan_interval_seconds=120)
    scheduler._session_factory = _FakeSessionFactory(object())
    scheduler._scan_service = _FakeScanService()
    scheduler._redis = _FakeRedis()

    records = [
        _FakeActiveProfileRecord(
            profile=_FakeProfile(vendor_profile_id="profile-1"),
            browser_host=_FakeBrowserHost(name="host-1"),
        ),
        _FakeActiveProfileRecord(
            profile=_FakeProfile(vendor_profile_id="profile-2"),
            browser_host=_FakeBrowserHost(name="host-2"),
        ),
    ]

    class _RepositoryWithProfiles:
        def __init__(self, session) -> None:
            self.session = session

        async def list_active_profiles(self):
            return records

    monkeypatch.setattr("apps.worker.scheduler.BrowserRepository", _RepositoryWithProfiles)

    await scheduler._run_cycle()

    assert scheduler._scan_service.calls == [
        ("profile-1", "host-1"),
        ("profile-2", "host-2"),
    ]


# Проверяет, что ошибка сканирования одного профиля не останавливает цикл и следующий профиль тоже обрабатывается.
@pytest.mark.asyncio
async def test_scheduler_cycle_continues_after_profile_error(monkeypatch) -> None:
    scheduler = object.__new__(SchedulerService)
    scheduler._settings = SimpleNamespace(worker_scan_interval_seconds=120)
    scheduler._session_factory = _FakeSessionFactory(object())

    class _PartiallyBrokenScanService:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def run_once(self, *, profile_id: str, browser_host_name: str):
            self.calls.append((profile_id, browser_host_name))
            if profile_id == "profile-1":
                raise RuntimeError("Падение первого профиля")
            return SimpleNamespace(rows_parsed=2, scan_run_id="scan-run-2")

    scheduler._scan_service = _PartiallyBrokenScanService()
    scheduler._redis = _FakeRedis()

    records = [
        _FakeActiveProfileRecord(
            profile=_FakeProfile(vendor_profile_id="profile-1"),
            browser_host=_FakeBrowserHost(name="host-1"),
        ),
        _FakeActiveProfileRecord(
            profile=_FakeProfile(vendor_profile_id="profile-2"),
            browser_host=_FakeBrowserHost(name="host-2"),
        ),
    ]

    class _RepositoryWithProfiles:
        def __init__(self, session) -> None:
            self.session = session

        async def list_active_profiles(self):
            return records

    monkeypatch.setattr("apps.worker.scheduler.BrowserRepository", _RepositoryWithProfiles)

    await scheduler._run_cycle()

    assert scheduler._scan_service.calls == [
        ("profile-1", "host-1"),
        ("profile-2", "host-2"),
    ]
