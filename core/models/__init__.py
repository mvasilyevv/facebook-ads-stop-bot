# -*- coding: utf-8 -*-
"""Доменно-разнесённые ORM-модели.

Таблицы PostgreSQL регистрируются здесь для Alembic и тестовой схемы.

При импорте этого пакета все ORM-классы регистрируются в Base.metadata,
что нужно для Alembic autogenerate и для create_all/drop_all в тестах.
"""

from __future__ import annotations

from core.models.auth import PanelLoginTicket, PanelOidcAttempt, PanelSessionRecord
from core.models.base import (
    Base,
    BigIntPrimaryKey,
    CreatedAtOnly,
    SingletonMixin,
    Timestamp,
    UUIDPrimaryKey,
)
from core.models.campaigns import (
    CampaignCreative,
    CampaignDraft,
    CampaignPreset,
    CampaignRun,
    OfferCreativeSeq,
)
from core.models.catalog import (
    AdAccount,
    FbAd,
    FbAdset,
    FbCampaign,
    Offer,
    OfferAdAccount,
    OfferRule,
)
from core.models.meta_api import (
    BrowserChannelReadiness,
    MetaAccountSnapshot,
    MetaApiAuditLog,
    MetaShadowSpendState,
)
from core.models.observer import (
    AdAlertState,
    AdMetrics,
    AlertEvent,
    ScanRun,
)
from core.models.operator import OperatorDisplayPreference, OperatorRevisionEvent
from core.models.settings import (
    AdoptionReceipt,
    AdsetProCredentials,
    ObserverConfig,
    SystemConfig,
    TelegramConfig,
    VisionConfig,
)
from core.models.tasks import (
    AdsetDuplicatePreview,
    BrowserOperationLease,
    CommandIdempotencyReceipt,
    TaskQueue,
)
from core.models.telegram import (
    Incident,
    NotificationDelivery,
    NotificationEvent,
    TelegramActionToken,
    TelegramCommandReply,
    TelegramInvite,
    TelegramMessageSlot,
    TelegramNavigationToken,
    TelegramRecipient,
    TelegramRecipientPreference,
    TelegramUpdateInbox,
)
from core.models.trackers import (
    AdsetProPostbackEvent,
    TrackerClickState,
)

__all__ = [
    # base
    "Base",
    "BigIntPrimaryKey",
    "CreatedAtOnly",
    "SingletonMixin",
    "Timestamp",
    "UUIDPrimaryKey",
    # auth
    "PanelLoginTicket",
    "PanelOidcAttempt",
    "PanelSessionRecord",
    # campaigns
    "CampaignCreative",
    "CampaignDraft",
    "CampaignPreset",
    "CampaignRun",
    "OfferCreativeSeq",
    # settings
    "AdoptionReceipt",
    "AdsetProCredentials",
    "ObserverConfig",
    "SystemConfig",
    "TelegramConfig",
    "VisionConfig",
    # catalog
    "AdAccount",
    "FbAd",
    "FbAdset",
    "FbCampaign",
    "Offer",
    "OfferAdAccount",
    "OfferRule",
    # observer
    "AdAlertState",
    "AdMetrics",
    "AlertEvent",
    "ScanRun",
    # operator
    "OperatorDisplayPreference",
    "OperatorRevisionEvent",
    # tasks
    "AdsetDuplicatePreview",
    "BrowserOperationLease",
    "CommandIdempotencyReceipt",
    "TaskQueue",
    # telegram
    "Incident",
    "NotificationDelivery",
    "NotificationEvent",
    "TelegramActionToken",
    "TelegramCommandReply",
    "TelegramInvite",
    "TelegramMessageSlot",
    "TelegramNavigationToken",
    "TelegramRecipient",
    "TelegramRecipientPreference",
    "TelegramUpdateInbox",
    # meta_api
    "BrowserChannelReadiness",
    "MetaAccountSnapshot",
    "MetaApiAuditLog",
    "MetaShadowSpendState",
    # trackers
    "AdsetProPostbackEvent",
    "TrackerClickState",
]
