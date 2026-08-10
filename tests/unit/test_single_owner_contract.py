"""The runtime and baseline enforce exactly one active Telegram owner."""

import inspect

from apps.api.routers.v1 import settings_telegram
from core.models.telegram.recipient import TelegramRecipient
from core.telegram import service


def test_recipient_model_has_single_active_owner_unique_index() -> None:
    index = next(
        item
        for item in TelegramRecipient.__table__.indexes
        if item.name == "uq_telegram_recipients_single_active_owner"
    )

    assert index.unique is True
    assert "role = 'owner'" in str(index.dialect_options["postgresql"]["where"])
    assert "revoked_at IS NULL" in str(index.dialect_options["postgresql"]["where"])


def test_owner_grants_share_one_serialized_fail_closed_boundary() -> None:
    invite_source = inspect.getsource(settings_telegram._ensure_active_owner_invite)
    consume_source = inspect.getsource(service.consume_invite_and_create_recipient)

    assert "lock_owner_roster" in invite_source
    assert "status_code=409" in invite_source
    assert "lock_owner_roster" in consume_source
    assert "OR NOT EXISTS" in consume_source
    assert "r.role = 'owner'" in consume_source
