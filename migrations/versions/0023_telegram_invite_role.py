"""telegram_invites.role — роль создаваемого recipient (owner/recipient)."""

import sqlalchemy as sa
from alembic import op

revision = "0023_telegram_invite_role"
down_revision = "0022_creative_adset_meta"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "telegram_invites",
        sa.Column("role", sa.String(16), nullable=False, server_default=sa.text("'recipient'")),
    )
    # Backfill из метки created_by='cli:role=owner' (старые invite'ы до этой миграции)
    op.execute("UPDATE telegram_invites SET role='owner' WHERE created_by LIKE '%role=owner%'")


def downgrade() -> None:
    op.drop_column("telegram_invites", "role")
