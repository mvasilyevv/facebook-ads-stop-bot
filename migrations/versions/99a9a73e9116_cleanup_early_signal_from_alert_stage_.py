"""cleanup_early_signal_from_alert_stage_enum

Revision ID: 99a9a73e9116
Revises: f3facad02893
Create Date: 2026-04-24 10:12:29.779613
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "99a9a73e9116"
down_revision: str | None = "f3facad02893"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # Заменяем EARLY_SIGNAL на WARNING в обеих таблицах
    op.execute("UPDATE alert_events SET stage = 'WARNING' WHERE stage::text = 'EARLY_SIGNAL'")
    op.execute(
        "UPDATE ad_snapshots SET current_stage = 'WARNING' WHERE current_stage::text = 'EARLY_SIGNAL'"
    )

    # Пересоздаём enum без EARLY_SIGNAL
    op.execute("ALTER TYPE alert_stage_enum RENAME TO alert_stage_enum_old")
    op.execute("CREATE TYPE alert_stage_enum AS ENUM ('WARNING', 'STOP')")
    op.execute(
        "ALTER TABLE alert_events ALTER COLUMN stage TYPE alert_stage_enum USING stage::text::alert_stage_enum"
    )
    op.execute(
        "ALTER TABLE ad_snapshots ALTER COLUMN current_stage TYPE alert_stage_enum USING current_stage::text::alert_stage_enum"
    )
    op.execute("DROP TYPE alert_stage_enum_old")


def downgrade() -> None:
    # Восстанавливаем enum с EARLY_SIGNAL
    op.execute("ALTER TYPE alert_stage_enum RENAME TO alert_stage_enum_old")
    op.execute("CREATE TYPE alert_stage_enum AS ENUM ('WARNING', 'STOP', 'EARLY_SIGNAL')")
    op.execute(
        "ALTER TABLE alert_events ALTER COLUMN stage TYPE alert_stage_enum USING stage::text::alert_stage_enum"
    )
    op.execute(
        "ALTER TABLE ad_snapshots ALTER COLUMN current_stage TYPE alert_stage_enum USING current_stage::text::alert_stage_enum"
    )
    op.execute("DROP TYPE alert_stage_enum_old")
