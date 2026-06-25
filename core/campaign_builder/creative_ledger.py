"""Per-offer аллокатор сквозной нумерации кодов креативов + запись реестра.

Все функции работают в переданной транзакции (AsyncConnection) — вызывающий
управляет commit/rollback. Аллокация атомарна (UPDATE ... RETURNING под row-lock):
параллельные launch одного оффера получают непересекающиеся диапазоны.
"""

from __future__ import annotations

import uuid

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection


async def peek_next_seq(conn: AsyncConnection, offer_code: str) -> int:
    """Текущий high-water-mark номера кода (0 если по офферу ещё ничего не выдано)."""
    row = (
        await conn.execute(
            text("SELECT next_seq FROM offer_creative_seq WHERE offer_code = :c"),
            {"c": offer_code},
        )
    ).first()
    return int(row.next_seq) if row else 0


async def allocate_code_span(conn: AsyncConnection, offer_code: str, span: int) -> int:
    """Атомарно резервирует диапазон из span кодов, возвращает base (первый номер).

    span<=0 → возвращает (текущий next_seq + 1) без изменения счётчика.
    """
    if span <= 0:
        return await peek_next_seq(conn, offer_code) + 1
    new_seq = (
        await conn.execute(
            text(
                """
                INSERT INTO offer_creative_seq (offer_code, next_seq)
                VALUES (:c, :span)
                ON CONFLICT (offer_code) DO UPDATE
                    SET next_seq = offer_creative_seq.next_seq + EXCLUDED.next_seq
                RETURNING next_seq
                """
            ),
            {"c": offer_code, "span": span},
        )
    ).scalar_one()
    return int(new_seq) - span + 1


async def record_creative(
    conn: AsyncConnection,
    *,
    offer_code: str,
    code: str,
    kind: str,
    meta_creative_id: str,
    run_id: str | uuid.UUID | None,
) -> None:
    """Append-only запись созданного креатива (идемпотентно по (offer_code, code))."""
    await conn.execute(
        text(
            """
            INSERT INTO campaign_creative (offer_code, code, kind, meta_creative_id, run_id)
            VALUES (:offer, :code, :kind, :cid, :run_id)
            ON CONFLICT (offer_code, code) DO NOTHING
            """
        ),
        {
            "offer": offer_code,
            "code": code,
            "kind": kind,
            "cid": meta_creative_id,
            "run_id": str(run_id) if run_id else None,
        },
    )
