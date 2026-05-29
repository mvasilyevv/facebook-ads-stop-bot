# -*- coding: utf-8 -*-
"""Сменить дефолт observer_config.act_via_api на TRUE (API — основной канал act).

Решение #39: Marketing API (pause_ad/activate_ad) проверен вживую (48 операций,
0 промахов) и точнее DOM (бьёт по ad_id, не мажет по кнопке) → делаем его каналом
по умолчанию. DOM-путь (disable_worker/enable_worker) остаётся спящим резервом:
переключается флагом обратно в FALSE без правки кода (фолбэк при сбое Graph API
на живой Vision-сессии).

UPDATE приводит существующую singleton-строку к новому дефолту — на этапе
разработки осознанных FALSE ещё не выставляли, все FALSE здесь — дефолтные.

Revision ID: 0008_act_via_api_default_true
Revises: 0007_observer_act_via_api
Create Date: 2026-05-29
"""

from __future__ import annotations

from typing import Sequence

from alembic import op

revision: str = "0008_act_via_api_default_true"
down_revision: str | None = "0007_observer_act_via_api"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute("ALTER TABLE observer_config ALTER COLUMN act_via_api SET DEFAULT TRUE;")
    # Приводим текущий singleton к новому дефолту (API основной).
    op.execute("UPDATE observer_config SET act_via_api = TRUE WHERE act_via_api = FALSE;")


def downgrade() -> None:
    op.execute("ALTER TABLE observer_config ALTER COLUMN act_via_api SET DEFAULT FALSE;")
