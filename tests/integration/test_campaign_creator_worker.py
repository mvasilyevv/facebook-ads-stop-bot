# -*- coding: utf-8 -*-
"""Интеграционные тесты campaign_creator_worker — реальная БД (изолированная).

Проверяет полный путь воркера: claim задачи campaign_create → load campaign_run →
execute (с ЗАМОКАННЫМИ ExecuteGraphCall/MediaUploader, без ffmpeg) → запись
status/progress/created_meta_ids в campaign_run и финализация task_queue.

Сценарии:
- успех: run=succeeded + created_meta_ids, task=succeeded;
- partial-fail (часть объектов создана) → run=failed + created_meta_ids (осиротевшие),
  task=failed БЕЗ retry (money-safety: повтор = дубль кампании);
- transient-сбой → задача в retrying (run остаётся в работе);
- терминальный run (дубль-задача после reconciler) — повторно не исполняется.

ВНИМАНИЕ (cross-stream): тесту нужен task_queue.task_type CHECK с 'campaign_create'
(ORM core/models/tasks/task_queue.py + миграция — стрим data-layer). До его добавления
INSERT задачи упадёт по CHECK — это ожидаемая зависимость между стримами.

НЕ гонять на боевой :5433 — нужен изолированный <POSTGRES_DB>_test (фикстура pg_engine).
"""

from __future__ import annotations

import json
import uuid

import pytest
import pytest_asyncio
from sqlalchemy import text

from apps.campaign_creator_worker import (
    claim_campaign_task,
    finalize_run_cancelled,
    finalize_run_failed,
    finalize_run_succeeded,
)
from apps.campaign_creator_worker.main import process_one_task
from core.meta_api.browser_readiness import (
    BrowserReadinessObservation,
    load_vision_readiness_identity,
    persist_browser_readiness,
)
from core.meta_api.errors import (
    BrowserReadinessRejectedError,
    PermanentError,
    SessionUnavailableError,
)
from core.tasks.queue import create_task

# ---------------------- конфиг-снимок ----------------------


def _run_config() -> dict:
    """Минимальный валидный снимок CampaignConfig для одного блока с 1 adset."""
    return {
        "account": {
            "act_id": "123456789",
            "page_id": "111",
            "pixel_id": "222",
            "timezone_name": "Africa/Accra",
            "currency": "USD",
            "account_context_observed_at": "2026-07-29T08:30:00Z",
        },
        "offer_code": "GH_CR",
        "destination_link": "https://example.shop/x",
        "start_date": "2026-06-18",
        "creo_root": "/nonexistent-creo-root",
        "targeting": {"countries": ["GH"]},
        "budget": {
            "level": "campaign",
            "currency": "USD",
            "daily_amount": "50.00",
            "bid_strategy": "COST_CAP",
            "bid_amount": "1.50",
        },
        "campaigns": [
            {
                "key": "static",
                "name": "{byer} | {offer} | static | adset.pro | {date}",
                # The reviewed campaign contract has one source of truth for
                # cardinality: explicit media-store references, never a glob.
                "concept_refs": ["c0.jpg", "c1.jpg"],
                "adsets": [
                    {
                        "name": "{byer} | {offer} | static | s1 | {date}",
                        "dir": "static",
                        "glob": "*.jpg",
                    }
                ],
            }
        ],
    }


# ---------------------- моки канала ----------------------


class _FakeClient:
    """Замоканный MetaApiClient.execute_graph_call с авто-id."""

    def __init__(self, fail_on: str | None = None, error: Exception | None = None):
        self.calls: list[str] = []
        self._n = 0
        self._fail_on = fail_on
        self._error = error or PermanentError("rejected")

    async def execute_graph_call(self, *, method, endpoint, body_json=None, ad_account_id=None):
        self.calls.append(endpoint)
        # endswith, а не substring: '/ads' иначе матчит и '/adsets'.
        if self._fail_on and endpoint.endswith(self._fail_on):
            raise self._error
        self._n += 1
        return {"id": f"obj-{self._n}"}


class _FakeUploader:
    async def upload_image(self, ad_account_id, image_bytes, *, filename="upload.jpg", **kw):
        return "img-hash"

    async def upload_video_from_bytes(self, ad_account_id, video_bytes, *, filename="upload.mp4"):
        return "vid-id"

    async def wait_video_ready(self, video_id, **kw):
        return True

    async def get_video_thumbnail_url(self, video_id, **kw):
        return f"https://thumb.example/{video_id}.jpg"


@pytest.fixture(autouse=True)
def _patch_concepts(monkeypatch):
    """Заглушка резолвера концептов (нет реальных файлов на диске) + uniquify image."""
    from core.campaign_builder.uniquify import ConceptInput

    def fake_resolve(cfg):
        # 2 концепта на единственный блок → adset получит 2 ad.
        return {
            "static": [
                ConceptInput(concept_id="c0", kind="image", content=b"a", filename="c0.jpg"),
                ConceptInput(concept_id="c1", kind="image", content=b"b", filename="c1.jpg"),
            ]
        }

    monkeypatch.setattr(
        "apps.campaign_creator_worker.main.resolve_concepts_from_config", fake_resolve
    )
    monkeypatch.setattr(
        "core.campaign_builder.uniquify.uniquify_image_bytes", lambda content, **kw: b"jpeg"
    )


@pytest_asyncio.fixture
async def clean_campaigns(pg_engine):
    """Чистит campaign_run + campaign_create задачи до и после теста."""
    readiness_writer = uuid.uuid4()

    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(
                text(
                    """
                    DELETE FROM notification_events
                    WHERE incident_id IN (
                        SELECT incident.id
                        FROM incidents AS incident
                        JOIN campaign_run AS run
                          ON incident.resource_type = 'campaign_run'
                         AND incident.resource_id = CAST(run.id AS TEXT)
                    )
                    """
                )
            )
            await conn.execute(
                text(
                    """
                    DELETE FROM incidents
                    WHERE resource_type = 'campaign_run'
                      AND resource_id IN (SELECT CAST(id AS TEXT) FROM campaign_run)
                    """
                )
            )
            await conn.execute(text("DELETE FROM task_queue WHERE task_type = 'campaign_create'"))
            await conn.execute(text("DELETE FROM campaign_run"))

    await _truncate()
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                INSERT INTO vision_config (
                  x_token_encrypted,
                  profile_id,
                  singleton_key
                )
                VALUES (
                  'synthetic-campaign-test-token',
                  'campaign-test-profile',
                  'default'
                )
                ON CONFLICT (singleton_key) DO UPDATE
                SET profile_id = EXCLUDED.profile_id,
                    updated_at = clock_timestamp()
                """
            )
        )
    identity = await load_vision_readiness_identity(pg_engine)
    assert identity is not None
    assert await persist_browser_readiness(
        pg_engine,
        identity=identity,
        observation=BrowserReadinessObservation(
            state="ready",
            reason_code="ready",
            observed_contract_version=5,
            observed_profile_id=identity.profile_id,
            observed_session_id="campaign-test-session",
        ),
        writer_instance=readiness_writer,
        ttl_seconds=30,
    )
    yield
    await _truncate()
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                """
                DELETE FROM browser_channel_readiness
                WHERE writer_instance = :writer_instance
                """
            ),
            {"writer_instance": readiness_writer},
        )


async def _seed_run(pg_engine, config: dict, idem: str) -> str:
    """Создаёт campaign_run(queued) + возвращает его id (uuid str)."""
    run_id = str(uuid.uuid4())
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "INSERT INTO campaign_run (id, config, status, idempotency_key) "
                "VALUES (:id, CAST(:cfg AS JSONB), 'queued', :ik)"
            ),
            {"id": run_id, "cfg": json.dumps(config), "ik": idem},
        )
    return run_id


async def _seed_task(pg_engine, run_id: str, idem: str) -> int:
    """Создаёт task_queue(campaign_create, pending) → возвращает id."""
    task_id = await create_task(
        pg_engine,
        task_type="campaign_create",
        idempotency_key=idem,
        payload={"run_id": run_id},
        requested_by="test",
    )
    assert task_id is not None
    return task_id


# ---------------------- тесты ----------------------


# Успешный прогон: claim → execute → run=succeeded + created_meta_ids, task=succeeded.
@pytest.mark.asyncio
async def test_worker_success(pg_engine, clean_campaigns):
    idem = f"idem-{uuid.uuid4().hex[:8]}"
    run_id = await _seed_run(pg_engine, _run_config(), idem)
    task_id = await _seed_task(pg_engine, run_id, idem)

    claim = await claim_campaign_task(pg_engine)
    assert claim.task is not None and claim.task.id == task_id

    await process_one_task(pg_engine, claim.task, client=_FakeClient(), uploader=_FakeUploader())

    async with pg_engine.connect() as conn:
        run = (
            await conn.execute(
                text("SELECT status, created_meta_ids FROM campaign_run WHERE id = :rid"),
                {"rid": run_id},
            )
        ).first()
        task_status = (
            await conn.execute(
                text("SELECT status FROM task_queue WHERE id = :tid"), {"tid": task_id}
            )
        ).scalar()

    assert run.status == "succeeded"
    cmi = run.created_meta_ids
    if isinstance(cmi, str):
        cmi = json.loads(cmi)
    assert len(cmi["campaigns"]) == 1
    assert len(cmi["adsets"]) == 1
    assert len(cmi["ads"]) == 2  # 2 концепта → 2 ad в единственном adset
    assert task_status == "succeeded"


# Partial-fail: упало на создании ad (кампания+adset+creative уже в Meta) →
# run=failed + осиротевшие created_meta_ids, task=failed без retry.
@pytest.mark.asyncio
async def test_worker_partial_fail(pg_engine, clean_campaigns):
    idem = f"idem-{uuid.uuid4().hex[:8]}"
    run_id = await _seed_run(pg_engine, _run_config(), idem)
    task_id = await _seed_task(pg_engine, run_id, idem)

    claim = await claim_campaign_task(pg_engine)
    # Падаем на создании ad (всё до него уже создано).
    client = _FakeClient(fail_on="/ads", error=PermanentError("ad rejected"))
    await process_one_task(pg_engine, claim.task, client=client, uploader=_FakeUploader())

    async with pg_engine.connect() as conn:
        run = (
            await conn.execute(
                text("SELECT status, error, created_meta_ids FROM campaign_run WHERE id = :rid"),
                {"rid": run_id},
            )
        ).first()
        task_status = (
            await conn.execute(
                text("SELECT status FROM task_queue WHERE id = :tid"), {"tid": task_id}
            )
        ).scalar()

    assert run.status == "failed"
    assert "partial_fail" in (run.error or "")
    cmi = run.created_meta_ids
    if isinstance(cmi, str):
        cmi = json.loads(cmi)
    assert cmi["campaigns"]  # кампания осиротела — для ручной чистки
    # Money-safety: НЕ retry (failed терминально), повтор создал бы дубль.
    assert task_status == "failed"


# HIGH-2 money-safety: сбой НА POST campaign (Vision лёг при отправке → ответ мог
# потеряться, кампания создаться) → задача в failed БЕЗ retry (повтор = дубль), даже
# если причина transient. created пуст, но run уведён в failed (orphan на проверку).
@pytest.mark.asyncio
async def test_worker_fail_on_campaign_post_no_retry(pg_engine, clean_campaigns):
    idem = f"idem-{uuid.uuid4().hex[:8]}"
    run_id = await _seed_run(pg_engine, _run_config(), idem)
    task_id = await _seed_task(pg_engine, run_id, idem)

    claim = await claim_campaign_task(pg_engine)
    client = _FakeClient(fail_on="/campaigns", error=SessionUnavailableError("no vision"))
    await process_one_task(pg_engine, claim.task, client=client, uploader=_FakeUploader())

    async with pg_engine.connect() as conn:
        task = (
            await conn.execute(
                text(
                    "SELECT status, attempt_count, external_started_at, result "
                    "FROM task_queue WHERE id = :tid"
                ),
                {"tid": task_id},
            )
        ).first()
        run_status = (
            await conn.execute(
                text("SELECT status FROM campaign_run WHERE id = :rid"), {"rid": run_id}
            )
        ).scalar()
    # POST campaign инициирован → ack-lost → failed без retry (НЕ retrying).
    assert task.status == "failed"
    assert task.external_started_at is not None
    result = task.result if isinstance(task.result, dict) else json.loads(task.result)
    assert result["outcome"] == "UNKNOWN"
    assert result["manual_review_required"] is True
    assert run_status == "failed"


@pytest.mark.asyncio
async def test_worker_presend_readiness_rejection_requeues_without_attempt_burn(
    pg_engine,
    clean_campaigns,
):
    idem = f"idem-{uuid.uuid4().hex[:8]}"
    run_id = await _seed_run(pg_engine, _run_config(), idem)
    task_id = await _seed_task(pg_engine, run_id, idem)

    claim = await claim_campaign_task(pg_engine)
    assert claim.task is not None
    assert claim.task.browser_readiness_generation is not None
    client = _FakeClient(
        fail_on="/campaigns",
        error=BrowserReadinessRejectedError("local circuit open before browser dispatch"),
    )
    await process_one_task(
        pg_engine,
        claim.task,
        client=client,
        uploader=_FakeUploader(),
    )

    async with pg_engine.connect() as conn:
        task = (
            await conn.execute(
                text(
                    """
                    SELECT status, attempt_count, external_started_at
                    FROM task_queue
                    WHERE id = :task_id
                    """
                ),
                {"task_id": task_id},
            )
        ).one()
        run = (
            await conn.execute(
                text(
                    """
                    SELECT status, progress
                    FROM campaign_run
                    WHERE id = :run_id
                    """
                ),
                {"run_id": run_id},
            )
        ).one()

    assert client.calls == ["/act_123456789/campaigns"]
    assert task.status == "retrying"
    assert task.attempt_count == 0
    assert task.external_started_at is None
    assert run.status == "queued"
    progress = run.progress if isinstance(run.progress, dict) else json.loads(run.progress)
    assert progress == {
        "stage": "queued",
        "reason": "browser_readiness_rejected",
    }


# Transient ДО инициации POST campaign (нет концептов на блок) НЕ должен задеть деньги:
# падение происходит до любого Meta-вызова. Тут causa — ValueError (permanent), проверяем
# лишь что повторного залива нет и задача терминальна (не зависает в running).
@pytest.mark.asyncio
async def test_worker_pre_post_failure_no_meta_calls(pg_engine, clean_campaigns, monkeypatch):
    idem = f"idem-{uuid.uuid4().hex[:8]}"
    run_id = await _seed_run(pg_engine, _run_config(), idem)
    task_id = await _seed_task(pg_engine, run_id, idem)

    # Резолвер концептов отдаёт пустой блок → падение на validate ДО POST campaign.
    monkeypatch.setattr(
        "apps.campaign_creator_worker.main.resolve_concepts_from_config", lambda cfg: {"static": []}
    )

    claim = await claim_campaign_task(pg_engine)
    client = _FakeClient()
    await process_one_task(pg_engine, claim.task, client=client, uploader=_FakeUploader())

    # Ни одного Meta-вызова (упали до создания campaign).
    assert client.calls == []
    async with pg_engine.connect() as conn:
        status = (
            await conn.execute(
                text("SELECT status FROM task_queue WHERE id = :tid"), {"tid": task_id}
            )
        ).scalar()
    # Терминальна (не зависла в running). Пустые концепты = permanent (ValueError) → failed.
    assert status == "failed"


# MID transient self-sabotage: transient-сбой ДО POST campaign → run сбрасывается обратно
# в 'queued' (не застревает в uniquifying), задача в retrying. Следующий claim той же
# задачи НЕ зарубается re-claim guard'ом ('run уже в работе') и переисполняет залив.
@pytest.mark.asyncio
async def test_worker_transient_pre_post_resets_run_to_queued(
    pg_engine, clean_campaigns, monkeypatch
):
    idem = f"idem-{uuid.uuid4().hex[:8]}"
    run_id = await _seed_run(pg_engine, _run_config(), idem)
    task_id = await _seed_task(pg_engine, run_id, idem)

    claim = await claim_campaign_task(pg_engine)
    # Transient ДО инициации POST campaign: падаем в build_uniquification_plan (чистый шаг
    # ПЕРЕД любым Meta-вызовом) с TemporaryError. irreversible_attempted=False (POST не
    # инициирован, объект гарантированно не создан) → classify == transient → requeue.
    from core.meta_api.errors import TemporaryError

    call_state = {"failed": False}

    def _flaky_plan(*args, **kwargs):
        # Первый прогон падает (transient), повторный — реальная раскладка.
        if not call_state["failed"]:
            call_state["failed"] = True
            raise TemporaryError("vision unavailable до POST")
        from core.campaign_builder.uniquify import build_uniquification_plan as _real

        return _real(*args, **kwargs)

    monkeypatch.setattr("core.campaign_builder.execute.build_uniquification_plan", _flaky_plan)

    client = _FakeClient()
    await process_one_task(pg_engine, claim.task, client=client, uploader=_FakeUploader())
    # Ни одного Meta-вызова: упали ДО POST campaign (объект не создан).
    assert client.calls == []

    async with pg_engine.connect() as conn:
        run_status = (
            await conn.execute(
                text("SELECT status FROM campaign_run WHERE id = :rid"), {"rid": run_id}
            )
        ).scalar()
        task = (
            await conn.execute(
                text(
                    "SELECT status, attempt_count, external_started_at "
                    "FROM task_queue WHERE id = :tid"
                ),
                {"tid": task_id},
            )
        ).first()
    # Run НЕ застрял в uniquifying — сброшен в queued (re-claim guard его не зарубит).
    assert run_status == "queued"
    assert task.status == "retrying"
    assert task.attempt_count == 1
    assert task.external_started_at is None

    # Повторный claim той же задачи (после backoff) — переисполняем, теперь успешно.
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("UPDATE task_queue SET available_at = NOW() - INTERVAL '1 second' WHERE id=:tid"),
            {"tid": task_id},
        )
    claim2 = await claim_campaign_task(pg_engine)
    assert claim2.task is not None and claim2.task.id == task_id
    await process_one_task(pg_engine, claim2.task, client=_FakeClient(), uploader=_FakeUploader())

    async with pg_engine.connect() as conn:
        run2 = (
            await conn.execute(
                text("SELECT status FROM campaign_run WHERE id = :rid"), {"rid": run_id}
            )
        ).scalar()
        task2 = (
            await conn.execute(
                text("SELECT status FROM task_queue WHERE id = :tid"), {"tid": task_id}
            )
        ).scalar()
    # Легитимный transient-retry прошёл: run залит, задача succeeded.
    assert run2 == "succeeded"
    assert task2 == "succeeded"


# Дубль-задача на уже succeeded run (после reconciler-таймаута) — повторно не исполняется.
@pytest.mark.asyncio
async def test_worker_skips_terminal_run(pg_engine, clean_campaigns):
    idem = f"idem-{uuid.uuid4().hex[:8]}"
    run_id = await _seed_run(pg_engine, _run_config(), idem)
    # Помечаем run succeeded заранее.
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("UPDATE campaign_run SET status = 'succeeded' WHERE id = :rid"), {"rid": run_id}
        )
    task_id = await _seed_task(pg_engine, run_id, f"{idem}-2")

    claim = await claim_campaign_task(pg_engine)
    client = _FakeClient()
    await process_one_task(pg_engine, claim.task, client=client, uploader=_FakeUploader())

    # Money-safety: execute НЕ вызывался (нет ни одного Meta-вызова).
    assert client.calls == []
    async with pg_engine.connect() as conn:
        task_status = (
            await conn.execute(
                text("SELECT status FROM task_queue WHERE id = :tid"), {"tid": task_id}
            )
        ).scalar()
    assert task_status == "succeeded"


# Cancel-гонка: run отменён до исполнения → воркер прерывается ДО создания объектов в Meta.
# Money-safety: ни одного Graph-вызова (нет призрачной кампании). Задача → failed (терминально,
# без re-claim). Покрывает terminal-guard (cancel до загрузки); узкое окно cancel-после-загрузки
# закрывает атомарный set_run_status(expect='queued') — см. test_set_run_status_expect_guard.
@pytest.mark.asyncio
async def test_worker_aborts_when_run_cancelled_before_start(
    pg_engine, clean_campaigns, _patch_concepts
):
    idem = f"idem-{uuid.uuid4().hex[:8]}"
    run_id = await _seed_run(pg_engine, _run_config(), idem)
    task_id = await _seed_task(pg_engine, run_id, idem)

    claim = await claim_campaign_task(pg_engine)
    assert claim.task is not None

    # Конкурентный cancel выиграл гонку, пока run был queued.
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("UPDATE campaign_run SET status = 'cancelled' WHERE id = :rid"), {"rid": run_id}
        )

    client = _FakeClient()
    await process_one_task(pg_engine, claim.task, client=client, uploader=_FakeUploader())

    async with pg_engine.connect() as conn:
        run_status = (
            await conn.execute(
                text("SELECT status FROM campaign_run WHERE id = :rid"), {"rid": run_id}
            )
        ).scalar()
        task_status = (
            await conn.execute(
                text("SELECT status FROM task_queue WHERE id = :tid"), {"tid": task_id}
            )
        ).scalar()

    # Ни одного Graph-вызова (кампания НЕ создана) — призрака нет.
    assert client.calls == []
    # run остался cancelled (воркер не перевёл в uniquifying), задача терминальна (failed).
    assert run_status == "cancelled"
    assert task_status == "failed"


# Атомарный guard set_run_status(expect=...): переход не проходит, если статус уже другой
# (конкурентный cancel перевёл run в cancelled) → воркер прерывается без создания. Сердце
# фикса cancel-гонки на узком окне cancel-после-загрузки-до-uniquifying.
@pytest.mark.asyncio
async def test_set_run_status_expect_guard(pg_engine, clean_campaigns):
    from apps.campaign_creator_worker import set_run_status

    idem = f"idem-{uuid.uuid4().hex[:8]}"
    run_id = await _seed_run(pg_engine, _run_config(), idem)  # status=queued
    task_id = await _seed_task(pg_engine, run_id, f"{idem}-task")
    claim = await claim_campaign_task(pg_engine)
    assert claim.task is not None and claim.task.id == task_id

    # queued→uniquifying проходит (статус совпал с expect).
    assert (
        await set_run_status(
            pg_engine,
            run_id,
            "uniquifying",
            task=claim.task,
            expect="queued",
        )
        is True
    )
    # Повторный expect='queued' уже НЕ проходит (статус uniquifying) — возврат False.
    assert (
        await set_run_status(
            pg_engine,
            run_id,
            "creating",
            task=claim.task,
            expect="queued",
        )
        is False
    )
    async with pg_engine.connect() as conn:
        st = (
            await conn.execute(text("SELECT status FROM campaign_run WHERE id = :r"), {"r": run_id})
        ).scalar()
    # Статус не изменён неудавшимся переходом.
    assert st == "uniquifying"


@pytest.mark.asyncio
async def test_campaign_unknown_without_ids_commits_incident_with_task(
    pg_engine,
    clean_campaigns,
) -> None:
    idem = f"unknown-incident-{uuid.uuid4().hex[:8]}"
    run_id = await _seed_run(pg_engine, _run_config(), idem)
    task_id = await _seed_task(pg_engine, run_id, f"{idem}-task")
    claim = await claim_campaign_task(pg_engine)
    assert claim.task is not None and claim.task.id == task_id

    applied = await finalize_run_failed(
        pg_engine,
        run_id,
        task=claim.task,
        error="Meta response lost after external boundary",
        task_result={
            "outcome": "UNKNOWN",
            "reconcile_required": True,
            "reason": "external_result_ambiguous",
        },
    )

    assert applied is True
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT task.status AS task_status,
                           incident.status AS incident_status,
                           incident.correlation_id,
                           COUNT(event.id) AS event_count
                    FROM task_queue AS task
                    JOIN incidents AS incident
                      ON incident.incident_key = :incident_key
                    JOIN notification_events AS event
                      ON event.incident_id = incident.id
                    WHERE task.id = :task_id
                    GROUP BY task.status, incident.status,
                             incident.correlation_id, task.correlation_id
                    HAVING incident.correlation_id = task.correlation_id
                    """
                ),
                {
                    "task_id": task_id,
                    "incident_key": f"campaign-create:{run_id}:unknown",
                },
            )
        ).one()
    assert row.task_status == "failed"
    assert row.incident_status == "open"
    assert row.event_count == 1


@pytest.mark.asyncio
async def test_campaign_unknown_projection_failure_rolls_back_task_and_run(
    pg_engine,
    clean_campaigns,
    monkeypatch,
) -> None:
    import core.telegram.worker_notify as worker_notify

    idem = f"unknown-rollback-{uuid.uuid4().hex[:8]}"
    run_id = await _seed_run(pg_engine, _run_config(), idem)
    task_id = await _seed_task(pg_engine, run_id, f"{idem}-task")
    claim = await claim_campaign_task(pg_engine)
    assert claim.task is not None and claim.task.id == task_id

    async def fail_projection(*_args, **_kwargs):
        raise RuntimeError("simulated campaign incident boundary crash")

    monkeypatch.setattr(
        worker_notify,
        "notify_recurring_incident_in_transaction",
        fail_projection,
    )
    with pytest.raises(RuntimeError, match="campaign incident boundary"):
        await finalize_run_failed(
            pg_engine,
            run_id,
            task=claim.task,
            error="ambiguous",
            task_result={"outcome": "UNKNOWN", "reconcile_required": True},
        )

    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    """
                    SELECT run.status AS run_status, task.status AS task_status
                    FROM campaign_run AS run
                    JOIN task_queue AS task
                      ON task.payload->>'run_id' = CAST(run.id AS TEXT)
                    WHERE run.id = :run_id
                    """
                ),
                {"run_id": run_id},
            )
        ).one()
        incident_count = await conn.scalar(
            text("SELECT COUNT(*) FROM incidents WHERE incident_key = :incident_key"),
            {"incident_key": f"campaign-create:{run_id}:unknown"},
        )
    assert row.run_status == "queued"
    assert row.task_status == "running"
    assert incident_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["succeeded", "failed", "cancelled"])
async def test_expired_campaign_lease_cannot_finalize_run_or_task(
    pg_engine,
    clean_campaigns,
    terminal: str,
) -> None:
    idem = f"expired-fence-{terminal}-{uuid.uuid4().hex[:8]}"
    run_id = await _seed_run(pg_engine, _run_config(), idem)
    task_id = await _seed_task(pg_engine, run_id, f"{idem}-task")
    claim = await claim_campaign_task(pg_engine)
    assert claim.task is not None and claim.task.id == task_id
    async with pg_engine.begin() as conn:
        await conn.execute(
            text(
                "UPDATE task_queue SET lease_expires_at = NOW() - INTERVAL '1 second' "
                "WHERE id = :task_id"
            ),
            {"task_id": task_id},
        )

    if terminal == "succeeded":
        applied = await finalize_run_succeeded(
            pg_engine,
            run_id,
            task=claim.task,
            created_meta_ids={"campaigns": ["meta-1"]},
            progress={"stage": "succeeded"},
        )
    elif terminal == "failed":
        applied = await finalize_run_failed(
            pg_engine,
            run_id,
            task=claim.task,
            error="ambiguous",
            task_result={"outcome": "UNKNOWN", "reconcile_required": True},
        )
    else:
        applied = await finalize_run_cancelled(
            pg_engine,
            run_id,
            task=claim.task,
            reason="operator cancel",
        )

    assert applied is False
    async with pg_engine.connect() as conn:
        row = (
            await conn.execute(
                text(
                    "SELECT r.status AS run_status, t.status AS task_status "
                    "FROM campaign_run r JOIN task_queue t "
                    "ON t.payload->>'run_id' = r.id::text "
                    "WHERE r.id = :run_id"
                ),
                {"run_id": run_id},
            )
        ).one()
    assert row.run_status == "queued"
    assert row.task_status == "running"
