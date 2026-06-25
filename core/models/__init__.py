# -*- coding: utf-8 -*-
"""Доменно-разнесённые ORM-модели.

35 таблиц в Postgres + 3 namespace в Redis (см. DB_REDESIGN.md).

При импорте этого пакета все ORM-классы регистрируются в Base.metadata,
что нужно для Alembic autogenerate и для create_all/drop_all в тестах.
"""

from __future__ import annotations

from core.models.ad_library import (
    AdLibraryAd,
    AdLibraryMedia,
    AdLibraryReport,
    AdLibraryScan,
    AdLibrarySnapshot,
    AdLibraryTier,
    AdLibraryWinnerArchive,
)
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
    OfferRuleStat,
)
from core.models.creator import CreatorPlan
from core.models.meta_api import (
    MetaApiAuditLog,
    MetaApiObservation,
    MetaApiWebhookEvent,
)
from core.models.observer import (
    AdAlertState,
    AdAutoEnableDisabled,
    AdDepositCorrection,
    AdMetrics,
    AlertEvent,
    CabinetDayArchive,
    ScanRun,
)
from core.models.settings import (
    AdsetProCredentials,
    ObserverConfig,
    SystemConfig,
    TelegramConfig,
    VisionConfig,
)
from core.models.tasks import EnableRecommendation, TaskQueue
from core.models.telegram import (
    TelegramInvite,
    TelegramMessageRef,
    TelegramRecipient,
)
from core.models.trackers import (
    AdsetProPostbackEvent,
    TrackerAggregate,
    TrackerPostback,
)

__all__ = [
    # base
    "Base",
    "BigIntPrimaryKey",
    "CreatedAtOnly",
    "SingletonMixin",
    "Timestamp",
    "UUIDPrimaryKey",
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
    "OfferRuleStat",
    # observer
    "AdAlertState",
    "AdAutoEnableDisabled",
    "AdDepositCorrection",
    "AdMetrics",
    "AlertEvent",
    "CabinetDayArchive",
    "ScanRun",
    # tasks
    "EnableRecommendation",
    "TaskQueue",
    # telegram
    "TelegramInvite",
    "TelegramMessageRef",
    "TelegramRecipient",
    # creator
    "CreatorPlan",
    # ad_library
    "AdLibraryAd",
    "AdLibraryMedia",
    "AdLibraryReport",
    "AdLibraryScan",
    "AdLibrarySnapshot",
    "AdLibraryTier",
    "AdLibraryWinnerArchive",
    # meta_api
    "MetaApiAuditLog",
    "MetaApiObservation",
    "MetaApiWebhookEvent",
    # trackers
    "AdsetProPostbackEvent",
    "TrackerAggregate",
    "TrackerPostback",
]
