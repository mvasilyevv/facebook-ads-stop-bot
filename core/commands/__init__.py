"""Canonical command lifecycle shared by web, Telegram and automation."""

from core.commands.campaign_runs import (
    CampaignRunCommandReceipt,
    CampaignRunControlUnavailableError,
    CampaignRunIdempotencyConflictError,
    CampaignRunNotFoundError,
)
from core.commands.service import (
    CommandConflictError,
    CommandIdentityError,
    CommandNotFoundError,
    CommandPreconditionError,
    CommandReceipt,
    CommandService,
    principal_scoped_idempotency_key,
)

__all__ = [
    "CampaignRunCommandReceipt",
    "CampaignRunControlUnavailableError",
    "CampaignRunIdempotencyConflictError",
    "CampaignRunNotFoundError",
    "CommandConflictError",
    "CommandIdentityError",
    "CommandPreconditionError",
    "CommandNotFoundError",
    "CommandReceipt",
    "CommandService",
    "principal_scoped_idempotency_key",
]
