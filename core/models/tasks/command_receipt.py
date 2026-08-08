"""Durable aliases for operator command idempotency keys.

One money task may be observed through several channels (web, Telegram and
auto-pause), and campaign-run controls use the same ledger. Every accepted
client key is retained here so a replay still resolves to the original task
after that task leaves the active queue set.
"""

from __future__ import annotations

from sqlalchemy import BigInteger, CheckConstraint, ForeignKey, Index, String
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.schema import conv

from core.models.base import Base, CreatedAtOnly


class CommandIdempotencyReceipt(CreatedAtOnly, Base):
    """Immutable ``idempotency_key -> command task`` binding."""

    __tablename__ = "command_idempotency_receipts"

    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    task_id: Mapped[int] = mapped_column(
        BigInteger,
        ForeignKey("task_queue.id", ondelete="CASCADE"),
        nullable=False,
    )
    action_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    target_id: Mapped[str] = mapped_column(String(64), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "action_kind IN ("
            "'pause_ad', 'activate_ad', "
            "'abort_campaign_run', 'resume_campaign_run'"
            ")",
            name=conv("ck_command_idem_receipt_action"),
        ),
        Index("ix_command_idempotency_receipts_task_id", "task_id"),
    )


__all__ = ["CommandIdempotencyReceipt"]
