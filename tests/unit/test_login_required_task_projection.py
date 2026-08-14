from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import core.observer.login_required as login_required
import core.tasks.queue as queue


@pytest.mark.asyncio
async def test_failed_login_required_task_reuses_canonical_account_incident(monkeypatch) -> None:
    notify = AsyncMock(return_value=True)
    conn = object()
    monkeypatch.setattr(
        login_required,
        "notify_login_required_incident_in_transaction",
        notify,
    )

    await queue._project_facebook_login_incident_in_transaction(
        conn,
        task_type="meta_api_mutation",
        payload={"ad_account_id": "act_777"},
        result={"requires_facebook_login": True},
    )

    notify.assert_awaited_once_with(conn, ad_account_id="act_777")
