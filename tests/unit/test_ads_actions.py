"""Тесты сервисного слоя core/ads/actions.py — только моки, без реальной БД."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from core.ads.actions import (
    AdActionError,
    AdNotFoundError,
    claim_ad,
    disable_ad,
    get_ad_detail,
    snooze_ad,
)
from core.domain import AlertState


def _make_fb_ad(fb_ad_id: str = "123") -> MagicMock:
    ad = MagicMock()
    ad.id = uuid.uuid4()
    ad.fb_ad_id = fb_ad_id
    ad.ad_name = "Test Ad"
    return ad


def _make_snap(fb_ad_id: str = "123", token: str | None = "tok1") -> MagicMock:
    snap = MagicMock()
    snap.id = uuid.uuid4()
    snap.fb_ad_id = fb_ad_id
    snap.open_state_token = token
    snap.alert_state = AlertState.NORMAL
    snap.last_observed_at = datetime.now(UTC)
    snap.spend = None
    snap.leads = 0
    snap.deposits = 0
    snap.cpc = None
    snap.cost_per_lead = None
    snap.ctr = None
    snap.registrations = 0
    return snap


def _session_ctx(execute_results: list):
    """Возвращает патч get_session_factory с заданной последовательностью scalars().first()."""
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.rollback = AsyncMock()
    session.refresh = AsyncMock()

    results_iter = iter(execute_results)

    async def fake_execute(_query):
        result = next(results_iter)
        mock_res = MagicMock()
        mock_res.scalars.return_value.first.return_value = result
        mock_res.scalars.return_value.all.return_value = result if isinstance(result, list) else []
        return mock_res

    session.execute = fake_execute
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock(return_value=session)
    return factory, session


# --- disable_ad ---


@pytest.mark.asyncio
async def test_disable_ad_creates_task():
    """disable_ad создаёт новую DisableTask и возвращает created_new=True."""
    fb_ad = _make_fb_ad()
    snap = _make_snap()

    factory, session = _session_ctx([fb_ad, snap])

    task_mock = MagicMock()
    task_mock.id = uuid.uuid4()

    async def fake_refresh(obj, attrs=None):
        if hasattr(obj, "fb_ad_id"):  # это DisableTask
            obj.id = task_mock.id

    session.refresh = fake_refresh

    with patch("core.ads.actions.get_session_factory", return_value=factory):
        result = await disable_ad(
            fb_ad_id="123",
            actor_telegram_user_id="777",
            actor_username="user1",
        )

    assert result["created_new"] is True
    assert result["ad_name"] == "Test Ad"


@pytest.mark.asyncio
async def test_disable_ad_duplicate_returns_existing():
    """Повторный вызов с тем же idempotency_key возвращает created_new=False."""
    from sqlalchemy.exc import IntegrityError

    fb_ad = _make_fb_ad()
    snap = _make_snap()

    existing_task = MagicMock()
    existing_task.id = uuid.uuid4()

    session = AsyncMock()
    session.add = MagicMock()
    session.rollback = AsyncMock()

    call_count = 0

    async def fake_execute(_query):
        nonlocal call_count
        call_count += 1
        mock_res = MagicMock()
        if call_count == 1:
            mock_res.scalars.return_value.first.return_value = fb_ad
        elif call_count == 2:
            mock_res.scalars.return_value.first.return_value = snap
        else:
            mock_res.scalars.return_value.first.return_value = existing_task
        return mock_res

    session.execute = fake_execute

    async def raise_integrity():
        raise IntegrityError("duplicate", {}, Exception())

    session.commit = raise_integrity
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)

    factory = MagicMock(return_value=session)

    with patch("core.ads.actions.get_session_factory", return_value=factory):
        result = await disable_ad(
            fb_ad_id="123",
            actor_telegram_user_id="777",
            actor_username="user1",
        )

    assert result["created_new"] is False


# --- snooze_ad ---


@pytest.mark.asyncio
async def test_snooze_ad_clamps_min():
    """snooze_ad с minutes=1 должен clamped до 5 минут."""
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=session)

    now = datetime.now(UTC)
    with patch("core.ads.actions.get_session_factory", return_value=factory):
        result = await snooze_ad(fb_ad_id="123", minutes=1, actor_telegram_user_id="777")

    # должно быть >= 5 минут от now
    assert result >= now + timedelta(minutes=5)


@pytest.mark.asyncio
async def test_snooze_ad_clamps_max():
    """snooze_ad с minutes=9999 должен clamp до 720 минут."""
    session = AsyncMock()
    session.add = MagicMock()
    session.commit = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=session)

    now = datetime.now(UTC)
    with patch("core.ads.actions.get_session_factory", return_value=factory):
        result = await snooze_ad(fb_ad_id="123", minutes=9999, actor_telegram_user_id="777")

    # должно быть <= 720 минут от now + небольшой зазор
    assert result <= now + timedelta(minutes=721)
    assert result >= now + timedelta(minutes=719)


@pytest.mark.asyncio
async def test_snooze_ad_inserts_record():
    """snooze_ad должен вызвать session.add с AlertSnooze и session.commit."""
    session = AsyncMock()
    added_objects = []
    session.add = lambda obj: added_objects.append(obj)
    session.commit = AsyncMock()
    session.__aenter__ = AsyncMock(return_value=session)
    session.__aexit__ = AsyncMock(return_value=False)
    factory = MagicMock(return_value=session)

    with patch("core.ads.actions.get_session_factory", return_value=factory):
        await snooze_ad(fb_ad_id="abc", minutes=30, actor_telegram_user_id="888")

    assert len(added_objects) == 1
    assert added_objects[0].fb_ad_id == "abc"
    session.commit.assert_awaited_once()


# --- claim_ad ---


@pytest.mark.asyncio
async def test_claim_ad_no_alert_raises():
    """claim_ad без активного алерта поднимает AdActionError."""
    fb_ad = _make_fb_ad()
    factory, session = _session_ctx([fb_ad, None])  # None = нет AlertEvent

    with patch("core.ads.actions.get_session_factory", return_value=factory):
        with pytest.raises(AdActionError, match="Нет активного алерта"):
            await claim_ad(fb_ad_id="123", actor_telegram_user_id="777")


# --- get_ad_detail ---


@pytest.mark.asyncio
async def test_get_ad_detail_not_found():
    """get_ad_detail без FbAd поднимает AdNotFoundError."""
    factory, session = _session_ctx([None])  # FbAd не найден

    with patch("core.ads.actions.get_session_factory", return_value=factory):
        with pytest.raises(AdNotFoundError):
            await get_ad_detail(fb_ad_id="nonexistent")
