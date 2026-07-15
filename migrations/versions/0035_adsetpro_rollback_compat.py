"""Keep AdSet.pro inbox compatible with the N-1 application rollback.

Revision ID: 0035_adsetpro_rollback_compat
Revises: 0034_event_driven_tracker

Migration 0034 canonicalized the inbox and added a strict positive-event CHECK.
The previous application release writes provider event values verbatim, though,
and server-release intentionally rolls application containers back without
downgrading the database.  Widen the CHECK for that one-release transition.
Rows remain canonical under the current application; aliases are accepted only
so an emergency N-1 application rollback cannot turn postbacks into HTTP 500s.
"""

from __future__ import annotations

from alembic import op

revision = "0035_adsetpro_rollback_compat"
down_revision = "0034_event_driven_tracker"
branch_labels = None
depends_on = None

CONSTRAINT_NAME = "ck_adsetpro_postback_events_adsetpro_event_type"
TRANSITION_EVENT_TYPES = (
    "registration",
    "ftd",
    "redeposit",
    "reg",
    "signup",
    "hold",
    "cpa_hold",
    "first_deposit",
    "first-deposit",
    "first deposit",
    "accept",
    "cpa_accept",
    "redep",
    "cpa_redep",
    "confirmed_deposit",
    "decline",
    "declined",
    "rejected",
    "trash",
    "baddep",
)


def _quoted(values: tuple[str, ...]) -> str:
    return ", ".join("'" + value.replace("'", "''") + "'" for value in values)


def upgrade() -> None:
    op.execute(f"ALTER TABLE adsetpro_postback_events DROP CONSTRAINT IF EXISTS {CONSTRAINT_NAME}")
    op.execute(
        f"""
        ALTER TABLE adsetpro_postback_events
        ADD CONSTRAINT {CONSTRAINT_NAME}
        CHECK (lower(trim(event_type)) IN ({_quoted(TRANSITION_EVENT_TYPES)}))
        NOT VALID
        """
    )
    op.execute(f"ALTER TABLE adsetpro_postback_events VALIDATE CONSTRAINT {CONSTRAINT_NAME}")


def downgrade() -> None:
    op.execute(f"ALTER TABLE adsetpro_postback_events DROP CONSTRAINT IF EXISTS {CONSTRAINT_NAME}")
    op.execute(
        f"""
        ALTER TABLE adsetpro_postback_events
        ADD CONSTRAINT {CONSTRAINT_NAME}
        CHECK (event_type IN ('registration', 'ftd', 'redeposit'))
        NOT VALID
        """
    )
    # Deliberately validate: a downgrade across this compatibility boundary is
    # an explicit operation and must stop rather than silently strand aliases
    # in a schema that claims to be at strict revision 0034.
    op.execute(f"ALTER TABLE adsetpro_postback_events VALIDATE CONSTRAINT {CONSTRAINT_NAME}")
