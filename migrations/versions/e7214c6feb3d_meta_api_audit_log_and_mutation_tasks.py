"""meta_api_audit_log and mutation_tasks

Revision ID: e7214c6feb3d
Revises: c3a1b2d4e5f6
Create Date: 2026-05-26 13:56:52.840468
"""

from __future__ import annotations

from typing import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "e7214c6feb3d"
down_revision: str | None = "c3a1b2d4e5f6"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- meta_api_audit_log ---
    # Append-only лог всех вызовов Marketing API.
    # BigInteger PK (не UUID) для высокочастотной вставки.
    op.create_table(
        "meta_api_audit_log",
        sa.Column("id", sa.BigInteger, primary_key=True, autoincrement=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("method", sa.String(8), nullable=False),
        sa.Column("endpoint", sa.String(255), nullable=False),
        sa.Column("params_json", JSONB, nullable=True),
        sa.Column("request_body_json", JSONB, nullable=True),
        sa.Column("response_status", sa.Integer, nullable=False, server_default="0"),
        sa.Column("response_json", JSONB, nullable=True),
        sa.Column("duration_ms", sa.Integer, nullable=False, server_default="0"),
        sa.Column("initiated_by", sa.String(64), nullable=False),
        sa.Column("error_code", sa.Integer, nullable=True),
        sa.Column("error_subcode", sa.Integer, nullable=True),
        sa.Column("session_id", sa.String(64), nullable=True),
        sa.Column("ad_account_id", sa.String(32), nullable=True),
    )

    # Индекс на created_at — основной для временны́х срезов
    op.create_index(
        "ix_meta_api_audit_log_created_at",
        "meta_api_audit_log",
        ["created_at"],
    )

    # Составной индекс для аудита по источнику + времени
    op.create_index(
        "ix_meta_api_audit_log_initiated_by_created_at",
        "meta_api_audit_log",
        ["initiated_by", "created_at"],
    )

    # Partial-индекс на ошибочные ответы (response_status >= 400)
    op.create_index(
        "ix_meta_api_audit_log_errors",
        "meta_api_audit_log",
        ["response_status", "created_at"],
        postgresql_where=sa.text("response_status >= 400"),
    )

    # Partial-индекс по аккаунту — только для записей с известным ad_account_id
    op.create_index(
        "ix_meta_api_audit_log_ad_account_created_at",
        "meta_api_audit_log",
        ["ad_account_id", "created_at"],
        postgresql_where=sa.text("ad_account_id IS NOT NULL"),
    )

    # --- meta_api_mutation_tasks ---
    # Outbox-таблица для всех write-операций через Marketing API.
    # UUID PK + created_at/updated_at как у DisableTask.
    op.create_table(
        "meta_api_mutation_tasks",
        sa.Column("id", sa.Uuid, primary_key=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
        sa.Column("mutation_kind", sa.String(32), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=False),
        sa.Column("ad_account_id", sa.String(32), nullable=False),
        sa.Column("payload_json", JSONB, nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="PENDING"),
        sa.Column("idempotency_key", sa.String(64), nullable=False),
        sa.Column("attempt_count", sa.Integer, nullable=False, server_default="0"),
        sa.Column("max_attempts", sa.Integer, nullable=False, server_default="5"),
        sa.Column("next_retry_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_error", sa.Text, nullable=True),
        sa.Column("error_code", sa.Integer, nullable=True),
        sa.Column("error_subcode", sa.Integer, nullable=True),
        sa.Column("requested_by", sa.String(64), nullable=False),
        sa.Column("approved_by", sa.String(64), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approval_telegram_message_id", sa.BigInteger, nullable=True),
        sa.Column("result_json", JSONB, nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        # CHECK constraint — допустимые статусы outbox-задачи
        sa.CheckConstraint(
            "status IN ('DRAFT', 'PENDING', 'RUNNING', 'SUCCESS', 'FAILED', 'CANCELLED')",
            name="ck_meta_api_mutation_tasks_status",
        ),
        # UNIQUE на idempotency_key — защита от дублей
        sa.UniqueConstraint("idempotency_key", name="uq_meta_api_mutation_tasks_idempotency_key"),
    )

    # Индекс для PostgresTaskQueue (status + next_retry_at)
    op.create_index(
        "ix_meta_api_mutation_tasks_queue",
        "meta_api_mutation_tasks",
        ["status", "next_retry_at"],
    )

    # Индекс для rate-limit мониторинга per account
    op.create_index(
        "ix_meta_api_mutation_tasks_account_status",
        "meta_api_mutation_tasks",
        ["ad_account_id", "status"],
    )

    # Индекс для аналитики по типу мутации
    op.create_index(
        "ix_meta_api_mutation_tasks_kind_status_created",
        "meta_api_mutation_tasks",
        ["mutation_kind", "status", "created_at"],
    )

    # Индекс для аудита по источнику запроса
    op.create_index(
        "ix_meta_api_mutation_tasks_requested_by_status",
        "meta_api_mutation_tasks",
        ["requested_by", "status", "created_at"],
    )


def downgrade() -> None:
    # Удаляем индексы meta_api_mutation_tasks
    op.drop_index("ix_meta_api_mutation_tasks_requested_by_status", "meta_api_mutation_tasks")
    op.drop_index("ix_meta_api_mutation_tasks_kind_status_created", "meta_api_mutation_tasks")
    op.drop_index("ix_meta_api_mutation_tasks_account_status", "meta_api_mutation_tasks")
    op.drop_index("ix_meta_api_mutation_tasks_queue", "meta_api_mutation_tasks")
    op.drop_table("meta_api_mutation_tasks")

    # Удаляем индексы meta_api_audit_log
    op.drop_index("ix_meta_api_audit_log_ad_account_created_at", "meta_api_audit_log")
    op.drop_index("ix_meta_api_audit_log_errors", "meta_api_audit_log")
    op.drop_index("ix_meta_api_audit_log_initiated_by_created_at", "meta_api_audit_log")
    op.drop_index("ix_meta_api_audit_log_created_at", "meta_api_audit_log")
    op.drop_table("meta_api_audit_log")
