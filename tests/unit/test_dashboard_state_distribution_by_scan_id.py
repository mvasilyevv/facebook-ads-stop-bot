# -*- coding: utf-8 -*-
"""Распределение alert_state на дашборде считается по last_scan_id, не по окну времени."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from core.domain import AlertState


def _result(rows):
    """Мок результата SQLAlchemy с .all() возвращающим переданные пары."""
    result = MagicMock()
    result.all.return_value = rows
    return result


# В батче последнего скана 40 объявлений NORMAL, 2 WARNING_SENT; "потерянных" из прошлого
# скана быть не должно — они не попадают в state_distribution.
@pytest.mark.asyncio
async def test_state_distribution_filters_by_current_scan_id():
    from apps.api.routers import dashboard as dash_module

    db = MagicMock()
    db.execute = AsyncMock(
        return_value=_result(
            [
                (AlertState.NORMAL, 40),
                (AlertState.WARNING_SENT, 2),
            ]
        )
    )

    distribution = await dash_module._build_state_distribution(db, current_scan_id=7)

    by_label = {item["state"]: item["count"] for item in distribution}
    assert by_label.get("Норма") == 40
    assert by_label.get("Предупреждение") == 2
    db.execute.assert_awaited_once()


# До первого скана current_scan_id == 0, распределение должно быть пустым,
# без обращения к БД.
@pytest.mark.asyncio
async def test_state_distribution_empty_before_first_scan():
    from apps.api.routers import dashboard as dash_module

    db = MagicMock()
    db.execute = AsyncMock(return_value=_result([]))

    distribution = await dash_module._build_state_distribution(db, current_scan_id=0)

    assert distribution == []
    db.execute.assert_not_called()
