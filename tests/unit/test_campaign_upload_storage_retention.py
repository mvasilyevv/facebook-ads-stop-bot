# -*- coding: utf-8 -*-
"""Юнит-покрытие для issue #190/#192: кто ещё «держит» upload-набор концептов.

``_run_holds_creo_root`` — единственное место, решающее, может ли
``_cleanup_upload_dir``/``_sweep_stale_upload_dirs``/``_enforce_upload_volume_cap``
унести папку с исходниками. Здесь проверяется чистая логика решения (единый
источник — ``core.commands.campaign_runs.resume_unavailable_reason``); сквозной
сценарий (два прогона на одном creo_root, реальные файлы, реальный воркер) — в
tests/integration/test_campaign_creator_worker.py.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import pytest

from apps.campaign_creator_worker import _run_holds_creo_root


def _config(creo_root: str) -> dict:
    """Валидный снимок CampaignConfig — тот же контракт, что и у резюмирования."""
    return {
        "account": {
            "act_id": "123",
            "page_id": "100",
            "pixel_id": "200",
            "timezone_name": "Europe/Kaliningrad",
            "currency": "EUR",
            "currency_exponent": 2,
            "account_context_observed_at": "2026-07-29T12:00:00+00:00",
        },
        "offer_code": "GH_CR",
        "destination_link": "https://example.com",
        "start_date": "2026-07-30",
        "creo_root": creo_root,
        "targeting": {"countries": ["GH"]},
        "budget": {
            "level": "campaign",
            "currency": "EUR",
            "daily_amount": "50.00",
            "bid_strategy": "COST_CAP",
            "bid_amount": "1.50",
        },
        "campaigns": [
            {
                "key": "static",
                "name": "{byer} | {offer} | static | {date}",
                "concept_refs": ["c0.jpg"],
                "adsets": [{"name": "{byer} | s1 | {date}", "dir": "static", "glob": "*.jpg"}],
            }
        ],
    }


def _resumable_config(tmp_path, monkeypatch) -> dict:
    """Валидный config + реальный media-checkpoint на диске (resume доступен)."""
    monkeypatch.setenv("CAMPAIGN_UPLOAD_ROOT", str(tmp_path))
    upload_dir = tmp_path / "upload-1"
    upload_dir.mkdir()
    (upload_dir / "c0.jpg").write_bytes(b"image")
    return _config("upload-1")


def _resumable_task() -> dict:
    return {
        "task_status": "failed",
        "external_started_at": None,
        "task_result": {"outcome": "REJECTED", "reason": "pre_external_attempts_exhausted"},
    }


@pytest.mark.parametrize(
    "run_status",
    ["queued", "uniquifying", "uploading", "creating"],
)
def test_active_run_always_holds_its_upload_dir(run_status: str) -> None:
    """Активный прогон ещё не дочитал файлы — держит набор независимо от task/config."""
    assert _run_holds_creo_root(
        run_status=run_status,
        run_config={},
        created_meta_ids={},
        task=None,
    )


def test_succeeded_run_never_holds_its_upload_dir(tmp_path, monkeypatch) -> None:
    """Успешный прогон больше не читает оригиналы — не держит набор."""
    config = _resumable_config(tmp_path, monkeypatch)
    assert not _run_holds_creo_root(
        run_status="succeeded",
        run_config=config,
        created_meta_ids={},
        task=_resumable_task(),
    )


def test_failed_run_holds_its_upload_dir_while_resumable(tmp_path, monkeypatch) -> None:
    """Failed с доступным «Повторить залив» держит набор (issue #192)."""
    config = _resumable_config(tmp_path, monkeypatch)
    assert _run_holds_creo_root(
        run_status="failed",
        run_config=config,
        created_meta_ids={},
        task=_resumable_task(),
    )


def test_cancelled_run_holds_its_upload_dir_while_resumable(tmp_path, monkeypatch) -> None:
    config = _resumable_config(tmp_path, monkeypatch)
    task = {
        "task_status": "cancelled",
        "external_started_at": None,
        "task_result": {"outcome": "REJECTED", "reason": "campaign_run_abort_before_execution"},
    }
    assert _run_holds_creo_root(
        run_status="cancelled",
        run_config=config,
        created_meta_ids={},
        task=task,
    )


@pytest.mark.parametrize(
    ("task", "created_meta_ids"),
    [
        # Постоянный (permanent) отказ — «Повторить залив» тем же config бессмыслен.
        (
            {
                "task_status": "failed",
                "external_started_at": None,
                "task_result": {
                    "outcome": "REJECTED",
                    "reason": "permanent_pre_external_failure",
                },
            },
            {},
        ),
        # После внешней границы — не pre-external checkpoint.
        (
            {
                "task_status": "failed",
                "external_started_at": datetime.now(UTC),
                "task_result": {
                    "outcome": "REJECTED",
                    "reason": "pre_external_attempts_exhausted",
                },
            },
            {},
        ),
        # Уже есть созданные Meta-объекты — не pre-external checkpoint.
        (_resumable_task(), {"campaigns": ["2385000001"]}),
        # Задача пропала — «Повторить залив» недоступен.
        (None, {}),
    ],
)
def test_failed_run_does_not_hold_its_upload_dir_when_not_resumable(
    task: dict | None, created_meta_ids: dict
) -> None:
    # Эти ветки решаются раньше media-checkpoint (см. resume_unavailable_reason) —
    # реальные файлы на диске не нужны, поэтому CAMPAIGN_UPLOAD_ROOT не трогаем.
    assert not _run_holds_creo_root(
        run_status="failed",
        run_config=_config("upload-does-not-exist"),
        created_meta_ids=created_meta_ids,
        task=task,
    )


def test_failed_run_does_not_hold_when_media_checkpoint_missing(tmp_path, monkeypatch) -> None:
    """Папка уже пропала (гонка/ручное вмешательство) — «держать» нечего."""
    monkeypatch.setenv("CAMPAIGN_UPLOAD_ROOT", str(tmp_path))
    assert not _run_holds_creo_root(
        run_status="failed",
        run_config=_config("never-uploaded"),
        created_meta_ids={},
        task=_resumable_task(),
    )


# --- предел по объёму не сносит то, что грузят прямо сейчас (#190) ---


def _fill(path, size_bytes: int) -> None:
    path.mkdir(parents=True, exist_ok=True)
    (path / "concept.mp4").write_bytes(b"x" * size_bytes)


@pytest.mark.asyncio
async def test_volume_cap_spares_staging_dir_and_fresh_upload(tmp_path, monkeypatch) -> None:
    """Свежий набор и служебный каталог загрузки переживают достижение предела.

    Папка появляется на диске в POST /upload раньше строки campaign_run, а
    частичная загрузка лежит в служебном `.{id}.{hex}.uploading` в том же корне.
    До фикса предел сносил и то и другое: «на набор никто не ссылается» читалось
    как «он ничей», хотя оператор грузил его прямо в этот момент.
    """
    import time as _time

    from apps.campaign_creator_worker import main as worker_main

    monkeypatch.setenv("CAMPAIGN_UPLOAD_ROOT", str(tmp_path))

    stale = tmp_path / "stale-set"
    fresh = tmp_path / "fresh-set"
    staging = tmp_path / ".fresh-set.abc123.uploading"
    for path in (stale, fresh, staging):
        _fill(path, 400)
    old = _time.time() - 10 * 86400
    os.utime(stale, (old, old))

    async def _no_refs(_engine):
        return set()

    monkeypatch.setattr(worker_main, "referenced_creo_roots", _no_refs)

    removed, _total = await worker_main._enforce_upload_volume_cap(object(), max_total_bytes=500)

    assert removed == 1
    assert not stale.exists(), "старый незанятый набор обязан уйти под предел"
    assert fresh.exists(), "свежий набор грузят прямо сейчас — его трогать нельзя"
    assert staging.exists(), "служебный каталог частичной загрузки — не набор"
