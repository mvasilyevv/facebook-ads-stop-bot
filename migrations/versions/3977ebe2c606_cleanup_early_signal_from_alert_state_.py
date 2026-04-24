"""cleanup_early_signal_from_alert_state_and_stream_enums

Revision ID: 3977ebe2c606
Revises: 99a9a73e9116
Create Date: 2026-04-24 10:16:59.331305
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "3977ebe2c606"
down_revision: str | None = "99a9a73e9116"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # --- alert_state_enum: убираем EARLY_SIGNAL_SENT ---
    op.execute(
        "UPDATE ad_snapshots SET alert_state = 'NORMAL' WHERE alert_state::text = 'EARLY_SIGNAL_SENT'"
    )
    op.execute("UPDATE alert_events SET state = 'NORMAL' WHERE state::text = 'EARLY_SIGNAL_SENT'")
    op.execute("ALTER TYPE alert_state_enum RENAME TO alert_state_enum_old")
    op.execute(
        "CREATE TYPE alert_state_enum AS ENUM ('NORMAL', 'WARNING_SENT', 'STOP_SENT', 'CLAIMED', 'DISABLED')"
    )
    op.execute("""
        ALTER TABLE ad_snapshots
        ALTER COLUMN alert_state TYPE alert_state_enum
        USING alert_state::text::alert_state_enum
    """)
    op.execute("""
        ALTER TABLE alert_events
        ALTER COLUMN state TYPE alert_state_enum
        USING state::text::alert_state_enum
    """)
    op.execute("DROP TYPE alert_state_enum_old")

    # --- telegram_notification_stream_enum: убираем EARLY ---
    op.execute("DELETE FROM telegram_message_refs WHERE stream_kind::text = 'EARLY'")
    op.execute(
        "ALTER TYPE telegram_notification_stream_enum RENAME TO telegram_notification_stream_enum_old"
    )
    op.execute(
        "CREATE TYPE telegram_notification_stream_enum AS ENUM ('WARNING', 'STOP', 'ENABLE')"
    )
    op.execute("""
        ALTER TABLE telegram_message_refs
        ALTER COLUMN stream_kind TYPE telegram_notification_stream_enum
        USING stream_kind::text::telegram_notification_stream_enum
    """)
    op.execute("DROP TYPE telegram_notification_stream_enum_old")


def downgrade() -> None:
    pass
