# -*- coding: utf-8 -*-
"""Добавить task_queue.created_by_chat_id для owner ACL над DRAFT-задачами.

Колонка хранит TG chat_id инициатора задачи. Для drafts от AI-tools через TG —
заполняется из callback_query.from.id. Для MCP/HTTP — может быть NULL,
тогда approve через TG разрешён только админу (TelegramRecipient.role='owner').

Revision ID: 0002_taskq_chat_id
Revises: 0001_adsetpro_volna_3
Create Date: 2026-05-27
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "0002_taskq_chat_id"
down_revision: str | None = "0001_adsetpro_volna_3"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Добавляем nullable BIGINT — старые draft'ы остаются с NULL, не ломаются.
    op.execute(
        """
        ALTER TABLE task_queue
        ADD COLUMN IF NOT EXISTS created_by_chat_id BIGINT NULL;
        """
    )
    # Индекс для быстрого поиска по owner (для будущего /drafts mine).
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_task_queue_created_by_chat
        ON task_queue (created_by_chat_id, status)
        WHERE created_by_chat_id IS NOT NULL;
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_task_queue_created_by_chat;")
    op.execute("ALTER TABLE task_queue DROP COLUMN IF EXISTS created_by_chat_id;")
