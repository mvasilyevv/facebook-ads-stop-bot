"""Добавляет scope keys для campaign/adset и расширяет бизнес-идентификаторы."""

from __future__ import annotations

import re

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260320_0002"
down_revision = "20260320_0001"
branch_labels = None
depends_on = None

_NON_KEY_CHARS = re.compile(r"[^\w]+", re.UNICODE)
_DASHES = re.compile(r"-+")


def _normalize_scope_fragment(value: str | None) -> str:
    normalized = (value or "").casefold().strip()
    normalized = _NON_KEY_CHARS.sub("-", normalized).replace("_", "-")
    normalized = _DASHES.sub("-", normalized).strip("-")
    return normalized or "unknown"


def _build_campaign_scope_key(name: str | None, fallback: str | None) -> str:
    return f"campaign:{_normalize_scope_fragment(name or fallback)}"


def _build_adset_scope_key(
    campaign_scope_key: str,
    name: str | None,
    fallback: str | None,
) -> str:
    return f"adset:{campaign_scope_key}:{_normalize_scope_fragment(name or fallback)}"


def upgrade() -> None:
    bind = op.get_bind()

    op.add_column("campaigns", sa.Column("scope_key", sa.String(length=255), nullable=True))
    campaigns_table = sa.table(
        "campaigns",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.String()),
        sa.column("fb_campaign_id", sa.String()),
        sa.column("scope_key", sa.String()),
    )
    campaign_rows = bind.execute(
        sa.select(
            campaigns_table.c.id,
            campaigns_table.c.name,
            campaigns_table.c.fb_campaign_id,
        )
    ).mappings()
    for row in campaign_rows:
        bind.execute(
            sa.update(campaigns_table)
            .where(campaigns_table.c.id == row["id"])
            .values(
                scope_key=_build_campaign_scope_key(
                    row["name"],
                    row["fb_campaign_id"],
                )
            )
        )
    op.alter_column(
        "campaigns",
        "scope_key",
        existing_type=sa.String(length=255),
        nullable=False,
    )
    op.create_index("ix_campaigns_scope_key", "campaigns", ["scope_key"], unique=True)
    op.alter_column(
        "campaigns",
        "fb_campaign_id",
        existing_type=sa.String(length=64),
        nullable=True,
    )

    op.add_column("adsets", sa.Column("scope_key", sa.String(length=255), nullable=True))
    adsets_table = sa.table(
        "adsets",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.String()),
        sa.column("fb_adset_id", sa.String()),
        sa.column("campaign_id", postgresql.UUID(as_uuid=True)),
        sa.column("scope_key", sa.String()),
    )
    campaign_scope_lookup = {
        row["id"]: row["scope_key"]
        for row in bind.execute(
            sa.select(campaigns_table.c.id, campaigns_table.c.scope_key)
        ).mappings()
    }
    adset_rows = bind.execute(
        sa.select(
            adsets_table.c.id,
            adsets_table.c.name,
            adsets_table.c.fb_adset_id,
            adsets_table.c.campaign_id,
        )
    ).mappings()
    for row in adset_rows:
        campaign_scope_key = campaign_scope_lookup.get(
            row["campaign_id"],
            "campaign:unknown",
        )
        bind.execute(
            sa.update(adsets_table)
            .where(adsets_table.c.id == row["id"])
            .values(
                scope_key=_build_adset_scope_key(
                    campaign_scope_key,
                    row["name"],
                    row["fb_adset_id"],
                )
            )
        )
    op.alter_column(
        "adsets",
        "scope_key",
        existing_type=sa.String(length=255),
        nullable=False,
    )
    op.create_index("ix_adsets_scope_key", "adsets", ["scope_key"], unique=True)
    op.alter_column(
        "adsets",
        "fb_adset_id",
        existing_type=sa.String(length=64),
        nullable=True,
    )

    with op.batch_alter_table("entity_offer_bindings") as batch_op:
        batch_op.alter_column(
            "entity_id",
            existing_type=sa.String(length=64),
            type_=sa.String(length=255),
            existing_nullable=False,
        )

    with op.batch_alter_table("control_flags") as batch_op:
        batch_op.alter_column(
            "entity_id",
            existing_type=sa.String(length=64),
            type_=sa.String(length=255),
            existing_nullable=False,
        )

    with op.batch_alter_table("cooldowns") as batch_op:
        batch_op.alter_column(
            "entity_id",
            existing_type=sa.String(length=64),
            type_=sa.String(length=255),
            existing_nullable=False,
        )


def downgrade() -> None:
    bind = op.get_bind()

    campaigns_table = sa.table(
        "campaigns",
        sa.column("fb_campaign_id", sa.String()),
    )
    if bind.execute(
        sa.select(sa.func.count())
        .select_from(campaigns_table)
        .where(campaigns_table.c.fb_campaign_id.is_(None))
    ).scalar_one():
        raise RuntimeError(
            "Откат невозможен: в campaigns уже есть записи без настоящего Facebook campaign id"
        )

    adsets_table = sa.table(
        "adsets",
        sa.column("fb_adset_id", sa.String()),
    )
    if bind.execute(
        sa.select(sa.func.count())
        .select_from(adsets_table)
        .where(adsets_table.c.fb_adset_id.is_(None))
    ).scalar_one():
        raise RuntimeError(
            "Откат невозможен: в adsets уже есть записи без настоящего Facebook adset id"
        )

    with op.batch_alter_table("cooldowns") as batch_op:
        batch_op.alter_column(
            "entity_id",
            existing_type=sa.String(length=255),
            type_=sa.String(length=64),
            existing_nullable=False,
        )

    with op.batch_alter_table("control_flags") as batch_op:
        batch_op.alter_column(
            "entity_id",
            existing_type=sa.String(length=255),
            type_=sa.String(length=64),
            existing_nullable=False,
        )

    with op.batch_alter_table("entity_offer_bindings") as batch_op:
        batch_op.alter_column(
            "entity_id",
            existing_type=sa.String(length=255),
            type_=sa.String(length=64),
            existing_nullable=False,
        )

    op.alter_column(
        "adsets",
        "fb_adset_id",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.drop_index("ix_adsets_scope_key", table_name="adsets")
    op.drop_column("adsets", "scope_key")

    op.alter_column(
        "campaigns",
        "fb_campaign_id",
        existing_type=sa.String(length=64),
        nullable=False,
    )
    op.drop_index("ix_campaigns_scope_key", table_name="campaigns")
    op.drop_column("campaigns", "scope_key")
