"""DEFAULT-партиции для всех RANGE-партиционированных таблиц.

Защита от ошибки «no partition of relation found for row»: если cleanup_worker
не успел создать месячную партицию до первого INSERT (старт месяца, даунтайм,
race), строка уходит в _default вместо того, чтобы потеряться. Это критично
для adsetpro_postback_events и ad_metrics — потеря означает недосчитанный
депозит и возможный ложный стоп прибыльного объявления.

Revision ID: 0031_default_partitions
Revises: 0030_drop_offer_default_cpa
"""

from alembic import op

revision = "0031_default_partitions"
down_revision = "0030_drop_offer_default_cpa"
branch_labels = None
depends_on = None

# Все RANGE-партиционированные таблицы проекта (core/models/ + migrations/0001)
_PARTITIONED_TABLES = [
    "ad_metrics",
    "alert_events",
    "scan_runs",
    "ad_library_snapshot",
    "meta_api_webhook_event",
    "meta_api_audit_log",
    "tracker_postback",
    "adsetpro_postback_events",
]


def upgrade() -> None:
    for table in _PARTITIONED_TABLES:
        op.execute(f"CREATE TABLE IF NOT EXISTS {table}_default PARTITION OF {table} DEFAULT")


def downgrade() -> None:
    for table in _PARTITIONED_TABLES:
        op.execute(f"DROP TABLE IF EXISTS {table}_default")
