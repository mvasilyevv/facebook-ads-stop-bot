"""DROP мёртвых forum_*_thread_id из telegram_config (убраны из ORM в Волне 2)."""

import sqlalchemy as sa
from alembic import op

revision = "0024_drop_forum_thread_columns"
down_revision = "0023_telegram_invite_role"
branch_labels = None
depends_on = None

# Пять мёртвых forum-колонок — chat_id НЕ трогаем
_COLS = [
    "forum_warning_thread_id",
    "forum_stop_thread_id",
    "forum_enable_thread_id",
    "forum_ops_thread_id",
    "forum_digest_thread_id",
]


def upgrade() -> None:
    for c in _COLS:
        op.drop_column("telegram_config", c)


def downgrade() -> None:
    # Возвращаем как nullable Integer (оригинальный тип)
    for c in _COLS:
        op.add_column("telegram_config", sa.Column(c, sa.Integer(), nullable=True))
