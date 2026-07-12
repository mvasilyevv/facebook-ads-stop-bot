"""tracker_aggregate.ad_id: CASCADE → SET NULL + nullable (M-9).

Аудит 2026-07-12. Hard-delete fb_ads каскадом удалял строки tracker_aggregate и
делал revenue-историю невосстановимой: adsetpro_postback_events выживают через
fb_ad_fk SET NULL, но aggregator исключает `fb_ad_fk IS NULL` → пересчитать нечем.
Меняем ondelete на SET NULL (+ ad_id nullable), чтобы агрегат-строка переживала
удаление ада (ad_id=NULL) и деньги не терялись из аналитики.

UNIQUE(ad_id, country, day) сохранён: NULL != NULL в Postgres, поэтому осиротевшие
строки не конфликтуют, а aggregator их не трогает (source WHERE fb_ad_fk IS NOT NULL).

Revision ID: 0033_tracker_aggregate_ad_id_set_null
Revises: 0032_tracker_revenue_scale
"""

from alembic import op

revision = "0033_tracker_agg_setnull"
down_revision = "0032_tracker_revenue_scale"
branch_labels = None
depends_on = None

_FK_NAME = "fk_tracker_aggregate_ad_id_fb_ads"


def upgrade() -> None:
    op.alter_column("tracker_aggregate", "ad_id", existing_type=None, nullable=True)
    op.drop_constraint(_FK_NAME, "tracker_aggregate", type_="foreignkey")
    op.create_foreign_key(
        _FK_NAME,
        "tracker_aggregate",
        "fb_ads",
        ["ad_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    # Осиротевшие строки (ad_id IS NULL) удаляем — иначе NOT NULL / CASCADE не встанут.
    op.execute("DELETE FROM tracker_aggregate WHERE ad_id IS NULL")
    op.drop_constraint(_FK_NAME, "tracker_aggregate", type_="foreignkey")
    op.create_foreign_key(
        _FK_NAME,
        "tracker_aggregate",
        "fb_ads",
        ["ad_id"],
        ["id"],
        ondelete="CASCADE",
    )
    op.alter_column("tracker_aggregate", "ad_id", nullable=False)
