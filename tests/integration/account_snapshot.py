# -*- coding: utf-8 -*-
"""Сохранить и вернуть строки ``meta_account_snapshot`` вокруг теста.

Фикстуры, которые сеют свой кабинет, обязаны вернуть чужую строку такой, какой
взяли. Пока каждая перечисляла колонки руками, список был копией схемы —
и `0008_account_status_evidence` эту копию обошёл: три фикстуры продолжали
снимать и восстанавливать шесть колонок, молча обнуляя статус кабинета
следующему тесту в том же прогоне. Ошибка тихая: восстановление ничем не
сверяется, падения нет.

Набор колонок берётся у самой БД, поэтому следующая миграция не заводит
четвёртую копию списка.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

from sqlalchemy import text

_TABLE = "meta_account_snapshot"


async def capture_account_snapshot_rows(conn: Any, account_ids: Sequence[str]) -> list[dict]:
    """Снимок строк кабинета целиком — со всеми колонками, какие есть в схеме."""
    rows = (
        await conn.execute(
            text(
                f"SELECT * FROM {_TABLE} "  # noqa: S608 — имя таблицы константное
                "WHERE account_id = ANY(CAST(:account_ids AS text[])) "
                "ORDER BY account_id"
            ),
            {"account_ids": list(account_ids)},
        )
    ).mappings()
    return [dict(row) for row in rows]


async def restore_account_snapshot_rows(conn: Any, rows: Sequence[Mapping[str, Any]]) -> None:
    """Вернуть снятые строки без потери колонок, появившихся после написания теста."""
    if not rows:
        return
    columns = list(rows[0].keys())
    column_list = ", ".join(columns)
    value_list = ", ".join(f":{name}" for name in columns)
    await conn.execute(
        text(
            f"INSERT INTO {_TABLE} ({column_list}) VALUES ({value_list})"  # noqa: S608
        ),
        [dict(row) for row in rows],
    )
