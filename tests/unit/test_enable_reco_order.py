# -*- coding: utf-8 -*-
"""enable_reco: при сбое TG mark_recommended НЕ ставится (рекомендация не теряется)."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import apps.enable_recommendation_worker.main as er


@pytest.fixture(autouse=True)
def _mock_web_app_url():
    """send_alert дёргает load_web_app_url(engine) для web_app deep-link кнопки.
    В unit-тестах engine — MagicMock, реальный SQL не выполнить → мокаем (None)."""
    with patch(
        "apps.enable_recommendation_worker.main.load_web_app_url",
        AsyncMock(return_value=None),
    ):
        yield


def _fake_candidate() -> er.CandidateRow:
    """Минимальный CandidateRow для тестов send_alert."""
    return er.CandidateRow(
        ad_id=uuid.uuid4(),
        fb_ad_id="act_123_456",
        ad_name="Test Ad",
        campaign_name="Test Campaign",
        adset_name="Test AdSet",
        alert_state="disabled",
        last_transition_at=__import__("datetime").datetime(
            2026, 6, 1, 0, 0, tzinfo=__import__("datetime").timezone.utc
        ),
        snoozed_until=None,
        offer_code="CR2",
        cpa_threshold=None,
        open_state_token=uuid.uuid4(),
    )


def _fake_decision():
    """Минимальный RecommendationDecision для тестов send_alert."""
    from core.enable_reco.analyzer import RecommendationDecision

    return RecommendationDecision(
        recommend=True,
        level="warning",
        skip_reason=None,
        snapshot={},
    )


# send_alert возвращает bool успеха
@pytest.mark.asyncio
async def test_send_alert_returns_bool():
    client = AsyncMock()
    client.send_message = AsyncMock(return_value={"ok": True})
    engine = MagicMock()
    fake_r = MagicMock()
    fake_r.chat_id = "1"
    with patch(
        "apps.enable_recommendation_worker.main.load_active_recipients",
        AsyncMock(return_value=[fake_r]),
    ):
        res = await er.send_alert(
            client,
            candidate=_fake_candidate(),
            decision=_fake_decision(),
            recommendation_id=uuid.uuid4(),
            engine=engine,
        )
    assert res is True


# send_alert упал → возвращает False (мок client кидает исключение)
@pytest.mark.asyncio
async def test_send_alert_failure_false():
    client = AsyncMock()
    client.send_message = AsyncMock(side_effect=RuntimeError("TG недоступен"))
    engine = MagicMock()
    fake_r = MagicMock()
    fake_r.chat_id = "1"
    with patch(
        "apps.enable_recommendation_worker.main.load_active_recipients",
        AsyncMock(return_value=[fake_r]),
    ):
        res = await er.send_alert(
            client,
            candidate=_fake_candidate(),
            decision=_fake_decision(),
            recommendation_id=uuid.uuid4(),
            engine=engine,
        )
    assert res is False


# send_alert с пустым списком recipients → False
@pytest.mark.asyncio
async def test_send_alert_no_recipients_false():
    client = AsyncMock()
    engine = MagicMock()
    with patch(
        "apps.enable_recommendation_worker.main.load_active_recipients",
        AsyncMock(return_value=[]),
    ):
        res = await er.send_alert(
            client,
            candidate=_fake_candidate(),
            decision=_fake_decision(),
            recommendation_id=uuid.uuid4(),
            engine=engine,
        )
    assert res is False


# send_alert при None client → возвращает False
@pytest.mark.asyncio
async def test_send_alert_none_client_false():
    engine = MagicMock()
    res = await er.send_alert(
        None,
        candidate=_fake_candidate(),
        decision=_fake_decision(),
        recommendation_id=uuid.uuid4(),
        engine=engine,
    )
    assert res is False


# При сбое TG mark_recommended НЕ вызывается (рекомендация не теряется)
@pytest.mark.asyncio
async def test_mark_recommended_not_called_on_send_failure():
    """Главный инвариант: сбой send_alert → mark_recommended пропускается."""
    call_log: list[str] = []

    async def fake_insert(*_, **__):
        return uuid.uuid4()

    async def fake_send(*_, **__):
        call_log.append("send")
        return False  # имитируем сбой TG

    async def fake_mark(redis_client, ad_id):
        call_log.append("mark")
        return True

    async def fake_candidates(*_, **__):
        return [_fake_candidate()]

    async def fake_metrics(*_, **__):
        return []

    async def fake_scanning(*_):
        return True

    from core.enable_reco.analyzer import RecommendationDecision

    def fake_should_recommend(**__):
        return RecommendationDecision(
            recommend=True, level="warning", skip_reason=None, snapshot={}
        )

    async def fake_is_recent(*_, **__):
        return False

    with (
        patch.object(er, "fetch_candidates", fake_candidates),
        patch.object(er, "fetch_metrics_since", fake_metrics),
        patch.object(er, "insert_recommendation", fake_insert),
        patch.object(er, "send_alert", fake_send),
        patch.object(er, "mark_recommended", fake_mark),
        patch.object(er, "is_recently_recommended", fake_is_recent),
        patch("apps.enable_recommendation_worker.main.load_scanning_enabled", fake_scanning),
        patch("apps.enable_recommendation_worker.main.should_recommend", fake_should_recommend),
    ):
        engine = MagicMock()
        counts = await er.run_once(
            engine,
            redis_client=AsyncMock(),
            tg_client=AsyncMock(),
        )

    # send был вызван, mark — нет
    assert "send" in call_log
    assert "mark" not in call_log
    # счётчик send_failed должен учитывать сбой
    assert counts.get("send_failed", 0) == 1
    assert counts.get("alerts_sent", 0) == 0


# При успешной отправке mark_recommended вызывается
@pytest.mark.asyncio
async def test_mark_recommended_called_on_send_success():
    """При успешном send_alert — mark_recommended ДОЛЖЕН быть вызван."""
    call_log: list[str] = []

    async def fake_insert(*_, **__):
        return uuid.uuid4()

    async def fake_send(*_, **__):
        call_log.append("send")
        return True  # успешная отправка

    async def fake_mark(redis_client, ad_id):
        call_log.append("mark")
        return True

    async def fake_candidates(*_, **__):
        return [_fake_candidate()]

    async def fake_metrics(*_, **__):
        return []

    async def fake_scanning(*_):
        return True

    from core.enable_reco.analyzer import RecommendationDecision

    def fake_should_recommend(**__):
        return RecommendationDecision(
            recommend=True, level="warning", skip_reason=None, snapshot={}
        )

    async def fake_is_recent(*_, **__):
        return False

    with (
        patch.object(er, "fetch_candidates", fake_candidates),
        patch.object(er, "fetch_metrics_since", fake_metrics),
        patch.object(er, "insert_recommendation", fake_insert),
        patch.object(er, "send_alert", fake_send),
        patch.object(er, "mark_recommended", fake_mark),
        patch.object(er, "is_recently_recommended", fake_is_recent),
        patch("apps.enable_recommendation_worker.main.load_scanning_enabled", fake_scanning),
        patch("apps.enable_recommendation_worker.main.should_recommend", fake_should_recommend),
    ):
        engine = MagicMock()
        counts = await er.run_once(
            engine,
            redis_client=AsyncMock(),
            tg_client=AsyncMock(),
        )

    # оба вызваны
    assert "send" in call_log
    assert "mark" in call_log
    assert counts.get("alerts_sent", 0) == 1


# LOW (аудит 02.07): при сбое TG rec-запись должна откатываться (re-arm), иначе
# idempotency_key блокирует повторную вставку навсегда и рекомендация теряется молча,
# пока ад не сменит состояние (last_transition_at не изменится — тот же ключ снова
# столкнётся с ON CONFLICT DO NOTHING).
@pytest.mark.asyncio
async def test_send_failure_deletes_unpromoted_recommendation_for_retry():
    """Сбой send_alert → insert откатывается delete_unpromoted_recommendation (re-arm)."""
    call_log: list[str] = []
    fixed_rec_id = uuid.uuid4()

    async def fake_insert(*_, **__):
        call_log.append("insert")
        return fixed_rec_id

    async def fake_send(*_, **__):
        call_log.append("send")
        return False  # имитируем сбой TG

    async def fake_delete(engine, *, rec_id):
        call_log.append(f"delete:{rec_id}")

    async def fake_mark(redis_client, ad_id):
        call_log.append("mark")
        return True

    async def fake_candidates(*_, **__):
        return [_fake_candidate()]

    async def fake_metrics(*_, **__):
        return []

    async def fake_scanning(*_):
        return True

    from core.enable_reco.analyzer import RecommendationDecision

    def fake_should_recommend(**__):
        return RecommendationDecision(
            recommend=True, level="warning", skip_reason=None, snapshot={}
        )

    async def fake_is_recent(*_, **__):
        return False

    with (
        patch.object(er, "fetch_candidates", fake_candidates),
        patch.object(er, "fetch_metrics_since", fake_metrics),
        patch.object(er, "insert_recommendation", fake_insert),
        patch.object(er, "send_alert", fake_send),
        patch.object(er, "delete_unpromoted_recommendation", fake_delete),
        patch.object(er, "mark_recommended", fake_mark),
        patch.object(er, "is_recently_recommended", fake_is_recent),
        patch("apps.enable_recommendation_worker.main.load_scanning_enabled", fake_scanning),
        patch("apps.enable_recommendation_worker.main.should_recommend", fake_should_recommend),
    ):
        engine = MagicMock()
        counts = await er.run_once(
            engine,
            redis_client=AsyncMock(),
            tg_client=AsyncMock(),
        )

    # insert → send (fail) → delete отката, mark_recommended не вызван
    assert call_log == ["insert", "send", f"delete:{fixed_rec_id}"]
    assert counts.get("send_failed", 0) == 1
    assert counts.get("alerts_sent", 0) == 0
