# -*- coding: utf-8 -*-
"""Unit-тесты: fast-stop ветка observer'а не должна терять WARNING-алерты
и non-STOP снэпшоты из progress-прохода.

Раньше при срабатывании быстрого стопа из progress-события observer:
- клал в alerts_to_send только STOP-алерты, отбрасывая WARNING из того же прохода
  (WARNING терялись на 10-30 секунд до следующего полного цикла);
- передавал в _process_scan_results пустой snapshot_batch, поэтому non-STOP
  снэпшоты не сохранялись и regression_guard терял baseline для следующего цикла.

Проверяется чистая функция _merge_progress_into_fast_stop, в которую вынесена
эта логика, чтобы не плодить тяжёлый сетап observer_loop.
"""

from __future__ import annotations

from types import SimpleNamespace

from apps.observer_worker.main import _merge_progress_into_fast_stop
from core.domain import AlertStage


def _alert(fb_ad_id: str, stage: AlertStage) -> SimpleNamespace:
    """Лёгкий заменитель AlertCandidate — нам нужны только fb_ad_id и stage."""
    return SimpleNamespace(fb_ad_id=fb_ad_id, stage=stage)


def _snap(fb_ad_id: str) -> dict:
    return {"fb_ad_id": fb_ad_id, "delivery_status": "ACTIVE"}


# Fast-stop не должен терять WARNING-алерты и не-STOP снэпшоты из progress-прохода.
def test_merge_progress_keeps_warning_alerts() -> None:
    """STOP+WARNING в одном progress-проходе: оба алерта попадают в alerts_to_send."""
    stop_alert = _alert("ad-stop", AlertStage.STOP)
    warning_alert = _alert("ad-warn", AlertStage.WARNING)

    progress_alerts = [warning_alert, stop_alert]
    progress_stop_alerts = [stop_alert]
    progress_snapshot_batch = [_snap("ad-warn"), _snap("ad-stop")]
    progress_ad_states = {"ad-stop": "state-stop", "ad-warn": "state-warn"}

    ad_states: dict = {}
    alerts_to_send: list = []
    stop_alerts: list = []
    snapshot_batch: list[dict] = []

    stop_ids = _merge_progress_into_fast_stop(
        progress_alerts=progress_alerts,
        progress_stop_alerts=progress_stop_alerts,
        progress_snapshot_batch=progress_snapshot_batch,
        progress_ad_states=progress_ad_states,
        ad_states=ad_states,
        alerts_to_send=alerts_to_send,
        stop_alerts=stop_alerts,
        snapshot_batch=snapshot_batch,
    )

    # WARNING-алерт не должен теряться.
    sent_ids = {a.fb_ad_id for a in alerts_to_send}
    assert "ad-warn" in sent_ids, "WARNING из progress-прохода должен остаться в alerts_to_send"
    assert "ad-stop" in sent_ids
    # stop_alerts — только реальные STOP.
    assert [a.fb_ad_id for a in stop_alerts] == ["ad-stop"]
    # ad_states обновляется только по STOP-идентификаторам.
    assert ad_states == {"ad-stop": "state-stop"}
    # stop_ids возвращается для использования вызывающей стороной.
    assert stop_ids == {"ad-stop"}


def test_merge_progress_keeps_nonstop_snapshots() -> None:
    """Snapshot_batch должен включать ВСЕ снэпшоты прохода, а не только STOP."""
    stop_alert = _alert("ad-stop", AlertStage.STOP)
    warning_alert = _alert("ad-warn", AlertStage.WARNING)

    progress_snapshot_batch = [_snap("ad-warn"), _snap("ad-stop"), _snap("ad-normal")]

    snapshot_batch: list[dict] = []
    _merge_progress_into_fast_stop(
        progress_alerts=[warning_alert, stop_alert],
        progress_stop_alerts=[stop_alert],
        progress_snapshot_batch=progress_snapshot_batch,
        progress_ad_states={},
        ad_states={},
        alerts_to_send=[],
        stop_alerts=[],
        snapshot_batch=snapshot_batch,
    )

    saved_ids = {row["fb_ad_id"] for row in snapshot_batch}
    # non-STOP запись нужна для baseline regression_guard.
    assert saved_ids == {"ad-warn", "ad-stop", "ad-normal"}


def test_merge_progress_aggregates_across_calls() -> None:
    """Если fast-stop сливает несколько progress-проходов подряд, аккумуляция работает."""
    a1 = _alert("ad-a", AlertStage.WARNING)
    a2 = _alert("ad-b", AlertStage.STOP)
    a3 = _alert("ad-c", AlertStage.WARNING)
    a4 = _alert("ad-d", AlertStage.STOP)

    ad_states: dict = {}
    alerts_to_send: list = []
    stop_alerts: list = []
    snapshot_batch: list[dict] = []

    # Первый проход
    _merge_progress_into_fast_stop(
        progress_alerts=[a1, a2],
        progress_stop_alerts=[a2],
        progress_snapshot_batch=[_snap("ad-a"), _snap("ad-b")],
        progress_ad_states={"ad-b": "s-b"},
        ad_states=ad_states,
        alerts_to_send=alerts_to_send,
        stop_alerts=stop_alerts,
        snapshot_batch=snapshot_batch,
    )
    # Второй проход
    _merge_progress_into_fast_stop(
        progress_alerts=[a3, a4],
        progress_stop_alerts=[a4],
        progress_snapshot_batch=[_snap("ad-c"), _snap("ad-d")],
        progress_ad_states={"ad-d": "s-d"},
        ad_states=ad_states,
        alerts_to_send=alerts_to_send,
        stop_alerts=stop_alerts,
        snapshot_batch=snapshot_batch,
    )

    assert {a.fb_ad_id for a in alerts_to_send} == {"ad-a", "ad-b", "ad-c", "ad-d"}
    assert {a.fb_ad_id for a in stop_alerts} == {"ad-b", "ad-d"}
    assert {row["fb_ad_id"] for row in snapshot_batch} == {"ad-a", "ad-b", "ad-c", "ad-d"}
    # ad_states обновляется только из STOP-записей.
    assert ad_states == {"ad-b": "s-b", "ad-d": "s-d"}
