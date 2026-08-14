# -*- coding: utf-8 -*-
"""Lower retention defaults on databases seeded by the baseline.

Рабочее значение политики живёт в system_config, поэтому одной правки
дефолтов в коде мало: база, созданная раньше, продолжила бы хранить
данные по прежним срокам. Ревизия правит только те ключи, которые всё
ещё равны исходному baseline: если оператор задал свой срок, он остаётся.
"""

from __future__ import annotations

from alembic import op

revision = "0003_tighten_retention_policy"
down_revision = "0002_operator_rule_context"
branch_labels = None
depends_on = None

# (ключ, прежний baseline, новый срок). Значения зафиксированы намеренно:
# миграция не должна меняться вслед за apps/cleanup_worker/retention.py.
_TIGHTENED = (
    ("ad_metrics", "90 days", "45 days"),
    ("alert_events", "365 days", "120 days"),
    ("adsetpro_postback_events", "60 days", "45 days"),
    ("task_queue_failed", "90 days", "45 days"),
    ("incidents_terminal", "365 days", "180 days"),
    ("notification_events_terminal", "365 days", "90 days"),
    ("telegram_action_tokens_terminal", "90 days", "45 days"),
    ("telegram_updates_terminal", "90 days", "30 days"),
    ("telegram_command_replies_terminal", "90 days", "30 days"),
)


def upgrade() -> None:
    changes = ", ".join(f"('{name}', '{old}', '{new}')" for name, old, new in _TIGHTENED)
    op.execute(
        f"""
        WITH changes(name, old_value, new_value) AS (VALUES {changes}),
        patch AS (
            SELECT COALESCE(jsonb_object_agg(c.name, to_jsonb(c.new_value)), '{{}}'::jsonb) AS value
            FROM changes c
            JOIN public.system_config s ON s.key = 'retention_policy'
            WHERE s.value ->> c.name = c.old_value
        )
        UPDATE public.system_config s
        SET value = s.value || (SELECT value FROM patch),
            updated_at = now()
        WHERE s.key = 'retention_policy'
        """
    )


def downgrade() -> None:
    raise RuntimeError("retention policy migration is forward-only")
