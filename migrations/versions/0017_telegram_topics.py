# -*- coding: utf-8 -*-
"""Форум-топик супергруппы под daily digest.

Добавляет telegram_config.forum_digest_thread_id — топик, в который шлётся
ежедневный дайджест (в дополнение к личкам получателей). Остальные топики
(стопы/предупреждения/включения/операции) уже были в схеме.

Revision ID: 0017_telegram_topics
Revises: 0016_drop_observer_act_via_api
Create Date: 2026-06-08
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "0017_telegram_topics"
down_revision: str | None = "0016_drop_observer_act_via_api"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE telegram_config "
        "ADD COLUMN IF NOT EXISTS forum_digest_thread_id INTEGER NULL;"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE telegram_config DROP COLUMN IF EXISTS forum_digest_thread_id;")
