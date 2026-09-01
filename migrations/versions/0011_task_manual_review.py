# -*- coding: utf-8 -*-
"""Ручная сверка задачи с неизвестным исходом (#360).

Терминальный UNKNOWN раньше не имел способа закрыться. Автосверка для
pause/activate исчерпывается (``result.reconciliation_exhausted``), а для
создания и дублирования запрещена архитектурно — повторять их вслепую нельзя.
Строка оставалась «требует ручной сверки» навсегда, и единственным способом
убрать баннер была правка ``result`` в БД руками.

Эти колонки — отдельная ось поверх исхода, по образцу
``incidents.acknowledged_at/acknowledged_by``: кто, когда и что именно увидел в
Ads Manager. ``result.outcome`` при этом остаётся UNKNOWN — внешняя операция
как была неизвестной, так и осталась.

Все колонки NULL-able; существующие строки не меняются, поведение до первой
операторской сверки бит-в-бит прежнее.
"""

from __future__ import annotations

from alembic import op

revision = "0011_task_manual_review"
down_revision = "0010_offer_rule_thresholds"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE task_queue
          ADD COLUMN IF NOT EXISTS manual_review_observation character varying(16) NULL,
          ADD COLUMN IF NOT EXISTS manual_review_at timestamp with time zone NULL,
          ADD COLUMN IF NOT EXISTS manual_review_by character varying(128) NULL
        """
    )
    # Наблюдение — зафиксированный факт из трёх, а не свободный текст: «ок»
    # ничего не значит, поэтому его нельзя записать в принципе.
    op.execute(
        """
        ALTER TABLE task_queue
          DROP CONSTRAINT IF EXISTS ck_task_queue_manual_review_observation
        """
    )
    op.execute(
        """
        ALTER TABLE task_queue
          ADD CONSTRAINT ck_task_queue_manual_review_observation
          CHECK (
            manual_review_observation IS NULL
            OR manual_review_observation IN ('stopped', 'active', 'missing')
          )
        """
    )
    # Кто и когда — не опциональная часть факта: сверка без автора и времени
    # неотличима от стёртого баннера.
    op.execute(
        """
        ALTER TABLE task_queue
          DROP CONSTRAINT IF EXISTS ck_task_queue_manual_review_complete
        """
    )
    op.execute(
        """
        ALTER TABLE task_queue
          ADD CONSTRAINT ck_task_queue_manual_review_complete
          CHECK (
            (manual_review_observation IS NULL
             AND manual_review_at IS NULL
             AND manual_review_by IS NULL)
            OR
            (manual_review_observation IS NOT NULL
             AND manual_review_at IS NOT NULL
             AND manual_review_by IS NOT NULL)
          )
        """
    )
    # Разбор «что ещё ждёт человека» не должен читать всю очередь.
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS ix_task_queue_manual_review_pending
          ON task_queue (updated_at DESC)
          WHERE manual_review_observation IS NULL
            AND result->>'outcome' = 'UNKNOWN'
        """
    )


def downgrade() -> None:
    raise RuntimeError("task manual review migration is forward-only")
