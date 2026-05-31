# -*- coding: utf-8 -*-
"""Добавить observer_config.campaign_ids — allowlist кампаний для am-режима.

campaign_ids — allowlist кампаний (#3): сужает am_tabular по campaign.id IN [...] в общем
кабинете. Owner-scoping (owner_campaign_tag) применяется в Python-пайплайне.
(scan_source убран: am_tabular — единственный источник, DOM-скан выпилен.)

Revision ID: 0011_observer_scan_source
Revises: 0010_fb_ads_delivery_status
Create Date: 2026-05-30
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "0011_observer_scan_source"
down_revision: str | None = "0010_fb_ads_delivery_status"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        "ALTER TABLE observer_config "
        "ADD COLUMN IF NOT EXISTS campaign_ids TEXT[] NOT NULL DEFAULT '{}';"
    )
    # scan_source выпилен (DOM не нужен): если колонка осталась от ранней версии — дропаем.
    op.execute("ALTER TABLE observer_config DROP COLUMN IF EXISTS scan_source;")


def downgrade() -> None:
    op.execute("ALTER TABLE observer_config DROP COLUMN IF EXISTS campaign_ids;")
