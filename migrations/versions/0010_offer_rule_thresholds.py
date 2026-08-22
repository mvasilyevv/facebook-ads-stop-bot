# -*- coding: utf-8 -*-
"""Настраиваемые базовые пороги стоп-правил per оффер (#260).

До этой миграции пороги (CPC 2%, CPL 10%, CPR 20%, 5 рег без депозита,
диапазоны 50-70%/70-90% от CPA, минимальный знаменатель 100) были зашиты
константами в коде. Числа расходятся в полтора-два раза между записями
корпуса по одному гео — байер не мог подвинуть их без релиза.

Все новые колонки NULL-able: NULL означает «не задано, берём константу-дефолт»,
поведение при пустых настройках не меняется бит-в-бит.
"""

from __future__ import annotations

from alembic import op

revision = "0010_offer_rule_thresholds"
down_revision = "0009_worker_heartbeats"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute(
        """
        ALTER TABLE offer_rules
          ADD COLUMN IF NOT EXISTS cpc_percent_of_cpa numeric(5, 2) NULL
            CONSTRAINT ck_offer_rules_cpc_percent_positive
              CHECK (cpc_percent_of_cpa IS NULL OR cpc_percent_of_cpa > 0),
          ADD COLUMN IF NOT EXISTS cpl_percent_of_cpa numeric(5, 2) NULL
            CONSTRAINT ck_offer_rules_cpl_percent_positive
              CHECK (cpl_percent_of_cpa IS NULL OR cpl_percent_of_cpa > 0),
          ADD COLUMN IF NOT EXISTS cpr_percent_of_cpa numeric(5, 2) NULL
            CONSTRAINT ck_offer_rules_cpr_percent_positive
              CHECK (cpr_percent_of_cpa IS NULL OR cpr_percent_of_cpa > 0),
          ADD COLUMN IF NOT EXISTS regs_no_dep_stop_count integer NULL
            CONSTRAINT ck_offer_rules_regs_count_positive
              CHECK (regs_no_dep_stop_count IS NULL OR regs_no_dep_stop_count > 0),
          ADD COLUMN IF NOT EXISTS spend_no_dep_from_percent numeric(5, 2) NULL
            CONSTRAINT ck_offer_rules_spend_no_dep_from_range
              CHECK (spend_no_dep_from_percent IS NULL
                     OR (spend_no_dep_from_percent > 0 AND spend_no_dep_from_percent <= 100)),
          ADD COLUMN IF NOT EXISTS spend_no_dep_to_percent numeric(5, 2) NULL
            CONSTRAINT ck_offer_rules_spend_no_dep_to_range
              CHECK (spend_no_dep_to_percent IS NULL
                     OR (spend_no_dep_to_percent > 0 AND spend_no_dep_to_percent <= 100)),
          ADD COLUMN IF NOT EXISTS spend_with_dep_from_percent numeric(5, 2) NULL
            CONSTRAINT ck_offer_rules_spend_with_dep_from_range
              CHECK (spend_with_dep_from_percent IS NULL
                     OR (spend_with_dep_from_percent > 0 AND spend_with_dep_from_percent <= 100)),
          ADD COLUMN IF NOT EXISTS spend_with_dep_to_percent numeric(5, 2) NULL
            CONSTRAINT ck_offer_rules_spend_with_dep_to_range
              CHECK (spend_with_dep_to_percent IS NULL
                     OR (spend_with_dep_to_percent > 0 AND spend_with_dep_to_percent <= 100)),
          ADD COLUMN IF NOT EXISTS min_ratio_denominator integer NULL
            CONSTRAINT ck_offer_rules_min_ratio_denominator_positive
              CHECK (min_ratio_denominator IS NULL OR min_ratio_denominator > 0)
        """
    )


def downgrade() -> None:
    raise RuntimeError("offer_rule_thresholds migration is forward-only")
