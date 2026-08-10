from __future__ import annotations

import uuid

import pytest
from sqlalchemy import text

from core.meta_api.queue import create_mutation_task
from core.meta_api.schemas import MetaMutationPayload


@pytest.mark.asyncio
async def test_telegram_origin_is_persisted_with_mutation_task(pg_engine) -> None:
    idempotency_key = f"test:telegram-origin:{uuid.uuid4()}"
    task_id = await create_mutation_task(
        pg_engine,
        payload=MetaMutationPayload(
            ad_account_id="123",
            mutation_kind="pause_ad",
            target_id="230011223344",
        ),
        requested_by="telegram:operator",
        idempotency_key=idempotency_key,
        created_by_chat_id=777,
    )
    assert task_id is not None

    try:
        async with pg_engine.connect() as conn:
            stored_chat_id = await conn.scalar(
                text("SELECT created_by_chat_id FROM task_queue WHERE id = :task_id"),
                {"task_id": task_id},
            )
        assert stored_chat_id == 777
    finally:
        async with pg_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM task_queue WHERE id = :task_id"),
                {"task_id": task_id},
            )
