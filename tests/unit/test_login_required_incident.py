from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import core.observer.login_required as login_required


@pytest.mark.asyncio
async def test_login_required_card_has_one_key_risk_and_concrete_action(monkeypatch) -> None:
    notify = AsyncMock(return_value=True)
    monkeypatch.setattr(login_required, "notify_recurring_incident", notify)

    accepted = await login_required.notify_login_required_incident(
        object(),
        ad_account_id="act_777",
    )

    assert accepted is True
    facts = notify.await_args.kwargs
    assert facts["incident_key"] == "observer:login_required:777"
    assert facts["title"] == "В Facebook нужно войти снова"
    assert facts["risk"] == "Пока не войдёшь, скан не идёт и авто-стоп не сработает"
    assert facts["lines"] == ["Открой удалённый рабочий стол и войди в Facebook"]
    assert facts["resource_type"] == "ad_account"
    assert facts["resource_id"] == "777"
