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

from apps.campaign_creator_worker import claim_campaign_task
from apps.campaign_creator_worker.main import process_one_task
from core.meta_api.errors import PermanentError, SessionUnavailableError

# ---------------------- конфиг-снимок ----------------------


def _run_config() -> dict:
    """Минимальный валидный снимок CampaignConfig для одного блока с 1 adset."""
    return {
        "account": {"act_id": "123456789", "page_id": "111", "pixel_id": "222"},
        "offer_code": "GH_CR",
        "destination_link": "https://example.shop/x",
        "start_date": "2026-06-18",
        "creo_root": "/nonexistent-creo-root",
        "targeting": {"countries": ["GH"]},
        "budget": {
            "level": "campaign",
            "daily_cents": 5000,
            "bid_strategy": "COST_CAP",
            "bid_amount_cents": 150,
        },
        "campaigns": [
            {
                "key": "static",
                "name": "{byer} | {offer} | static | adset.pro | {date}",
                "kind": "image",
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

    async def _truncate():
        async with pg_engine.begin() as conn:
            await conn.execute(text("DELETE FROM task_queue WHERE task_type = 'campaign_create'"))
            await conn.execute(text("DELETE FROM campaign_run"))

    await _truncate()
    yield
    await _truncate()


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
    async with pg_engine.begin() as conn:
        row = (
            await conn.execute(
                text(
                    "INSERT INTO task_queue "
                    "(task_type, status, idempotency_key, payload, attempt_count, "
                    " max_attempts, requested_by) "
                    "VALUES ('campaign_create', 'pending', :ik, CAST(:pl AS JSONB), 0, 5, 'test') "
                    "RETURNING id"
                ),
                {"ik": idem, "pl": json.dumps({"run_id": run_id})},
            )
        ).first()
    return int(row[0])


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
                text("SELECT status, attempt_count FROM task_queue WHERE id = :tid"),
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
    assert run_status == "failed"


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
                text("SELECT status, attempt_count FROM task_queue WHERE id = :tid"),
                {"tid": task_id},
            )
        ).first()
    # Run НЕ застрял в uniquifying — сброшен в queued (re-claim guard его не зарубит).
    assert run_status == "queued"
    assert task.status == "retrying"
    assert task.attempt_count == 1

    # Повторный claim той же задачи (после backoff) — переисполняем, теперь успешно.
    async with pg_engine.begin() as conn:
        await conn.execute(
            text("UPDATE task_queue SET next_retry_at = NOW() - INTERVAL '1 second' WHERE id=:tid"),
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

    # queued→uniquifying проходит (статус совпал с expect).
    assert await set_run_status(pg_engine, run_id, "uniquifying", expect="queued") is True
    # Повторный expect='queued' уже НЕ проходит (статус uniquifying) — возврат False.
    assert await set_run_status(pg_engine, run_id, "creating", expect="queued") is False
    async with pg_engine.connect() as conn:
        st = (
            await conn.execute(text("SELECT status FROM campaign_run WHERE id = :r"), {"r": run_id})
        ).scalar()
    # Статус не изменён неудавшимся переходом.
    assert st == "uniquifying"
