"""Добавляет запуски истории профиля и привязку scan run к запуску.

Revision ID: 20260323_0005
Revises: 20260322_0004
Create Date: 2026-03-23 16:10:00
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "20260323_0005"
down_revision = "20260322_0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "profile_launches",
        sa.Column("profile_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(["profile_id"], ["profiles.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_profile_launches")),
    )
    op.create_index(
        op.f("ix_profile_launches_profile_id"), "profile_launches", ["profile_id"], unique=False
    )
    op.create_index(
        op.f("ix_profile_launches_is_active"), "profile_launches", ["is_active"], unique=False
    )
    op.create_index(
        "uq_profile_launches_active_per_profile",
        "profile_launches",
        ["profile_id"],
        unique=True,
        sqlite_where=sa.text("is_active = 1"),
        postgresql_where=sa.text("is_active"),
    )

    op.add_column(
        "scan_runs",
        sa.Column("profile_launch_id", postgresql.UUID(as_uuid=True), nullable=True),
    )
    op.create_index(
        op.f("ix_scan_runs_profile_launch_id"), "scan_runs", ["profile_launch_id"], unique=False
    )
    op.create_foreign_key(
        op.f("fk_scan_runs_profile_launch_id_profile_launches"),
        "scan_runs",
        "profile_launches",
        ["profile_launch_id"],
        ["id"],
        ondelete="SET NULL",
    )

    bind = op.get_bind()
    profiles_table = sa.table(
        "profiles",
        sa.column("id", postgresql.UUID(as_uuid=True)),
    )
    profile_launches_table = sa.table(
        "profile_launches",
        sa.column("id", postgresql.UUID(as_uuid=True)),
        sa.column("profile_id", postgresql.UUID(as_uuid=True)),
        sa.column("name", sa.String(length=255)),
        sa.column("is_active", sa.Boolean()),
        sa.column("started_at", sa.DateTime(timezone=True)),
        sa.column("ended_at", sa.DateTime(timezone=True)),
        sa.column("created_at", sa.DateTime(timezone=True)),
        sa.column("updated_at", sa.DateTime(timezone=True)),
    )
    scan_runs_table = sa.table(
        "scan_runs",
        sa.column("profile_id", postgresql.UUID(as_uuid=True)),
        sa.column("profile_launch_id", postgresql.UUID(as_uuid=True)),
    )

    now = datetime.now(tz=UTC)
    launch_rows: list[dict[str, object]] = []
    for (profile_id,) in bind.execute(sa.select(profiles_table.c.id)).all():
        launch_rows.append(
            {
                "id": uuid.uuid4(),
                "profile_id": profile_id,
                "name": f"Миграционный запуск {now:%d.%m.%Y %H:%M}",
                "is_active": True,
                "started_at": now,
                "ended_at": None,
                "created_at": now,
                "updated_at": now,
            }
        )
    if launch_rows:
        op.bulk_insert(profile_launches_table, launch_rows)
        for launch_row in launch_rows:
            bind.execute(
                sa.update(scan_runs_table)
                .where(scan_runs_table.c.profile_id == launch_row["profile_id"])
                .values(profile_launch_id=launch_row["id"])
            )


def downgrade() -> None:
    op.drop_constraint(
        op.f("fk_scan_runs_profile_launch_id_profile_launches"),
        "scan_runs",
        type_="foreignkey",
    )
    op.drop_index(op.f("ix_scan_runs_profile_launch_id"), table_name="scan_runs")
    op.drop_column("scan_runs", "profile_launch_id")
    op.drop_index("uq_profile_launches_active_per_profile", table_name="profile_launches")
    op.drop_index(op.f("ix_profile_launches_is_active"), table_name="profile_launches")
    op.drop_index(op.f("ix_profile_launches_profile_id"), table_name="profile_launches")
    op.drop_table("profile_launches")
