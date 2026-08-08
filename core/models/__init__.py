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
    CampaignPreset,
    CampaignRun,
    OfferCreativeSeq,
)
from core.models.catalog import (
    FbAd,
    FbAdset,
    FbCampaign,
    Offer,
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
    AdAutoEnableDisabled,
    AdMetrics,
    AlertEvent,
    ScanRun,
)
from core.models.operator import OperatorRevisionEvent
from core.models.settings import (
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
    EnableRecommendation,
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
    "CampaignPreset",
    "CampaignRun",
    "OfferCreativeSeq",
    # settings
    "AdsetProCredentials",
    "ObserverConfig",
    "SystemConfig",
    "TelegramConfig",
    "VisionConfig",
    # catalog
    "FbAd",
    "FbAdset",
    "FbCampaign",
    "Offer",
    "OfferRule",
    # observer
    "AdAlertState",
    "AdAutoEnableDisabled",
    "AdMetrics",
    "AlertEvent",
    "ScanRun",
    # operator
    "OperatorRevisionEvent",
    # tasks
    "AdsetDuplicatePreview",
    "BrowserOperationLease",
    "CommandIdempotencyReceipt",
    "EnableRecommendation",
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
