# -*- coding: utf-8 -*-
"""Снимок кабинета хранит статус рекламного аккаунта.

До этой ревизии снимок знал только пояс и валюту, поэтому отключённый Meta
кабинет выглядел готовым к заливу: оператор видел зелёное, а первый же POST
возвращал «Отключенные аккаунты не могут создавать или редактировать рекламу»
(прод, 20.08.2026).

Колонки добавляются пустыми осознанно: у существующих строк статус НЕ
подтверждён, и подставлять им «активен» значило бы записать догадку как
свидетельство. Пустой статус читается как «неизвестно» и блокирует залив до
первого живого подтверждения Meta — его делает та же ручка контекста кабинета.
"""

from __future__ import annotations

from alembic import op

revision = "0008_account_status_evidence"
down_revision = "0007_preset_bid_and_link"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE public.meta_account_snapshot
            ADD COLUMN account_status smallint,
            ADD COLUMN account_status_observed_at timestamp with time zone
        """
    )


def downgrade() -> None:
    raise RuntimeError("ad account status evidence migration is forward-only")
