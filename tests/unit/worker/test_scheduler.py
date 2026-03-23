from __future__ import annotations

import asyncio
from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from apps.worker.scheduler import SchedulerService
from core.domain import ScanRunStatus
from core.locks import acquire_scan_lock


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
        self.renew_calls: list[tuple[str, int]] = []

    async def set(self, key: str, value: str, *, nx: bool = False, ex: int | None = None) -> bool:
        if nx and key in self._store:
            return False
        self._store[key] = value
        return True

    async def delete(self, key: str) -> int:
        return 1 if self._store.pop(key, None) is not None else 0

    async def eval(self, script: str, numkeys: int, *args):
        key = args[0]
        value = args[1]
        stored_value = self._store.get(key)
        if stored_value != value:
            return 0
        if len(args) > 2:
            ttl_seconds = int(args[2])
            self.renew_calls.append((key, ttl_seconds))
            return 1
        self._store.pop(key, None)
        return 1


class _FakeScanService:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    async def run_once(self, *, profile_id: str, browser_host_name: str):
        self.calls.append((profile_id, browser_host_name))
        return SimpleNamespace(
            rows_parsed=3,
            scan_run_id="scan-run-1",
            status=ScanRunStatus.SUCCEEDED,
        )


class _FakeOutboxProcessor:
    def __init__(self) -> None:
        self.calls: list[int] = []

    async def process_pending(self, batch_size: int = 50) -> int:
        self.calls.append(batch_size)
        return 0


# Проверяет, что scheduler не пытается запускать scanner, если в базе нет активных профилей.
@pytest.mark.asyncio
async def test_scheduler_cycle_skips_when_no_active_profiles(monkeypatch) -> None:
    scheduler = object.__new__(SchedulerService)
    scheduler._settings = SimpleNamespace(worker_scan_interval_seconds=120)
    scheduler._session_factory = _FakeSessionFactory(object())
    scheduler._redis = _FakeRedis()
    scheduler._resolve_runtime_settings = _fake_resolve_runtime_settings
    fake_scan_service = _FakeScanService()
    fake_outbox = _FakeOutboxProcessor()
    scheduler._build_scan_service = lambda settings: fake_scan_service
    scheduler._build_outbox_processor = lambda settings: fake_outbox

    class _EmptyBrowserRepository:
        def __init__(self, session) -> None:
            self.session = session

        async def list_active_profiles(self):
            return []

    monkeypatch.setattr("apps.worker.scheduler.BrowserRepository", _EmptyBrowserRepository)

    await scheduler._run_cycle()

    assert fake_scan_service.calls == []
    assert fake_outbox.calls == []


# Проверяет, что scheduler запускает реальный scan service для каждого активного профиля из базы.
@pytest.mark.asyncio
async def test_scheduler_cycle_scans_all_active_profiles(monkeypatch) -> None:
    scheduler = object.__new__(SchedulerService)
    scheduler._settings = SimpleNamespace(worker_scan_interval_seconds=120)
    scheduler._session_factory = _FakeSessionFactory(object())
    scheduler._redis = _FakeRedis()
    fake_scan_service = _FakeScanService()
    fake_outbox = _FakeOutboxProcessor()
    scheduler._resolve_runtime_settings = _fake_resolve_runtime_settings
    scheduler._build_scan_service = lambda settings: fake_scan_service
    scheduler._build_outbox_processor = lambda settings: fake_outbox

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

    assert fake_scan_service.calls == [
        ("profile-1", "host-1"),
        ("profile-2", "host-2"),
    ]
    assert fake_outbox.calls == [50]


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
            return SimpleNamespace(
                rows_parsed=2,
                scan_run_id="scan-run-2",
                status=ScanRunStatus.SUCCEEDED,
            )

    scheduler._redis = _FakeRedis()
    broken_scan_service = _PartiallyBrokenScanService()
    fake_outbox = _FakeOutboxProcessor()
    scheduler._resolve_runtime_settings = _fake_resolve_runtime_settings
    scheduler._build_scan_service = lambda settings: broken_scan_service
    scheduler._build_outbox_processor = lambda settings: fake_outbox

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

    assert broken_scan_service.calls == [
        ("profile-1", "host-1"),
        ("profile-2", "host-2"),
    ]
    assert fake_outbox.calls == [50]


# Проверяет, что scheduler корректно переживает пропущенный скан и продолжает обход профилей.
@pytest.mark.asyncio
async def test_scheduler_cycle_accepts_skipped_scan_result(monkeypatch) -> None:
    scheduler = object.__new__(SchedulerService)
    scheduler._settings = SimpleNamespace(worker_scan_interval_seconds=120)
    scheduler._session_factory = _FakeSessionFactory(object())
    scheduler._redis = _FakeRedis()

    class _SkippingScanService:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def run_once(self, *, profile_id: str, browser_host_name: str):
            self.calls.append((profile_id, browser_host_name))
            return SimpleNamespace(
                rows_parsed=0,
                scan_run_id=None,
                status=ScanRunStatus.SKIPPED,
            )

    skipping_scan_service = _SkippingScanService()
    fake_outbox = _FakeOutboxProcessor()
    scheduler._resolve_runtime_settings = _fake_resolve_runtime_settings
    scheduler._build_scan_service = lambda settings: skipping_scan_service
    scheduler._build_outbox_processor = lambda settings: fake_outbox

    records = [
        _FakeActiveProfileRecord(
            profile=_FakeProfile(vendor_profile_id="profile-1"),
            browser_host=_FakeBrowserHost(name="host-1"),
        ),
    ]

    class _RepositoryWithProfiles:
        def __init__(self, session) -> None:
            self.session = session

        async def list_active_profiles(self):
            return records

    monkeypatch.setattr("apps.worker.scheduler.BrowserRepository", _RepositoryWithProfiles)

    await scheduler._run_cycle()

    assert skipping_scan_service.calls == [("profile-1", "host-1")]
    assert fake_outbox.calls == [50]


# Проверяет, что блокировка сканирования продлевается во время работы и освобождается после выхода из контекста.
@pytest.mark.asyncio
async def test_scan_lock_heartbeat_renews_lock(monkeypatch) -> None:
    redis_client = _FakeRedis()

    async with acquire_scan_lock(redis_client, "profile-1", ttl_seconds=3):
        assert redis_client._store["scan_lock:profile:profile-1"] != ""
        await asyncio.sleep(1.2)
        assert redis_client.renew_calls

    assert "scan_lock:profile:profile-1" not in redis_client._store


# Проверяет, что scheduler честно пишет причину пропуска для стопнутого профиля.
@pytest.mark.asyncio
async def test_scheduler_cycle_logs_profile_suspended_skip_reason(monkeypatch, caplog) -> None:
    scheduler = object.__new__(SchedulerService)
    scheduler._settings = SimpleNamespace(worker_scan_interval_seconds=120)
    scheduler._session_factory = _FakeSessionFactory(object())
    scheduler._redis = _FakeRedis()

    class _SkippingScanService:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def run_once(self, *, profile_id: str, browser_host_name: str):
            self.calls.append((profile_id, browser_host_name))
            return SimpleNamespace(
                rows_parsed=0,
                scan_run_id=None,
                status=ScanRunStatus.SKIPPED,
                skip_reason="Профиль находится на стопе по причине `Ручной стоп`",
            )

    skipping_scan_service = _SkippingScanService()
    fake_outbox = _FakeOutboxProcessor()
    scheduler._resolve_runtime_settings = _fake_resolve_runtime_settings
    scheduler._build_scan_service = lambda settings: skipping_scan_service
    scheduler._build_outbox_processor = lambda settings: fake_outbox

    records = [
        _FakeActiveProfileRecord(
            profile=_FakeProfile(vendor_profile_id="profile-1"),
            browser_host=_FakeBrowserHost(name="host-1"),
        ),
    ]

    class _RepositoryWithProfiles:
        def __init__(self, session) -> None:
            self.session = session

        async def list_active_profiles(self):
            return records

    monkeypatch.setattr("apps.worker.scheduler.BrowserRepository", _RepositoryWithProfiles)

    with caplog.at_level("INFO"):
        await scheduler._run_cycle()

    assert skipping_scan_service.calls == [("profile-1", "host-1")]
    assert "Профиль находится на стопе по причине `Ручной стоп`" in caplog.text
    assert "автопауза и авторезюм выключены" not in caplog.text


# Проверяет, что scheduler честно пишет причину пропуска, когда выключены и действия, и режим наблюдения.
@pytest.mark.asyncio
async def test_scheduler_cycle_logs_actions_disabled_skip_reason(monkeypatch, caplog) -> None:
    scheduler = object.__new__(SchedulerService)
    scheduler._settings = SimpleNamespace(worker_scan_interval_seconds=120)
    scheduler._session_factory = _FakeSessionFactory(object())
    scheduler._redis = _FakeRedis()

    class _SkippingScanService:
        def __init__(self) -> None:
            self.calls: list[tuple[str, str]] = []

        async def run_once(self, *, profile_id: str, browser_host_name: str):
            self.calls.append((profile_id, browser_host_name))
            return SimpleNamespace(
                rows_parsed=0,
                scan_run_id=None,
                status=ScanRunStatus.SKIPPED,
                skip_reason="Автопауза, авторезюм и режим наблюдения выключены",
            )

    skipping_scan_service = _SkippingScanService()
    fake_outbox = _FakeOutboxProcessor()
    scheduler._resolve_runtime_settings = _fake_resolve_runtime_settings
    scheduler._build_scan_service = lambda settings: skipping_scan_service
    scheduler._build_outbox_processor = lambda settings: fake_outbox

    records = [
        _FakeActiveProfileRecord(
            profile=_FakeProfile(vendor_profile_id="profile-1"),
            browser_host=_FakeBrowserHost(name="host-1"),
        ),
    ]

    class _RepositoryWithProfiles:
        def __init__(self, session) -> None:
            self.session = session

        async def list_active_profiles(self):
            return records

    monkeypatch.setattr("apps.worker.scheduler.BrowserRepository", _RepositoryWithProfiles)

    with caplog.at_level("INFO"):
        await scheduler._run_cycle()

    assert skipping_scan_service.calls == [("profile-1", "host-1")]
    assert "Автопауза, авторезюм и режим наблюдения выключены" in caplog.text


# Проверяет, что scheduler берёт интервал сканирования из runtime-настроек без жёсткой привязки к env.
@pytest.mark.asyncio
async def test_scheduler_resolves_scan_interval_from_runtime_settings() -> None:
    scheduler = object.__new__(SchedulerService)
    scheduler._settings = SimpleNamespace(worker_scan_interval_seconds=120)

    async def _resolve_runtime_settings():
        return SimpleNamespace(worker_scan_interval_seconds=60)

    scheduler._resolve_runtime_settings = _resolve_runtime_settings

    interval = await scheduler._resolve_scan_interval_seconds()

    assert interval == 60


async def _fake_resolve_runtime_settings():
    return SimpleNamespace(worker_scan_interval_seconds=120)
