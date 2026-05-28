# -*- coding: utf-8 -*-
"""Добавить индекс (scan_id, created_at) на alert_events.

Без этого индекса dispatch_pending_alerts выполняет full-scan всех партиций
при поиске по scan_id — O(N * partitions). Индекс покрывает как scan_id-lookup,
так и обязательный partition-pruning по created_at.

Так как alert_events — RANGE-партиционированная таблица, CREATE INDEX IF NOT EXISTS
на parent-таблице автоматически создаёт соответствующий индекс на каждой существующей
дочерней партиции и на всех будущих партициях.

Revision ID: 0004_alert_events_scan_id_index
Revises: 0003_observer_auto_enable
Create Date: 2026-05-28
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "0004_alert_events_scan_id_index"
down_revision: str | None = "0003_observer_auto_enable"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Составной индекс (scan_id, created_at):
    # - scan_id — для поиска событий одного scan-цикла (lookup в dispatcher'е)
    # - created_at — обязателен для partition pruning (RANGE по created_at)
    # CONCURRENTLY нельзя использовать в транзакции Alembic; используем обычный CREATE.
    # На пустой БД или малом объёме — не критично. На проде с большой таблицей
    # рекомендуется выполнить вручную через CONCURRENTLY до применения миграции.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_alert_events_scan_id_created
        ON alert_events (scan_id, created_at);
        """
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_alert_events_scan_id_created;")
